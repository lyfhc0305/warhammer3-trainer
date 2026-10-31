-- Native recurring-income effects: the engine owns both prediction and payment.
-- Match campaign_manager:faction_has_campaign_feature: native context lookup
-- uses the faction's command queue index, not its database name. Do not assume
-- the optional global cco wrapper is a Lua function or even exists.
local function wh3pt_faction_context(f)
    assert(common and common.get_context_value, '战役上下文读取接口不可用')
    local cqi = f:command_queue_index()
    assert(type(cqi) == 'number' and cqi > 0 and cqi == math.floor(cqi), '玩家阵营编号无效')
    return {Call=function(_, expression)
        return common.get_context_value('CcoCampaignFaction', cqi, expression)
    end}
end
-- Apply native percentage effects once. No income probes, compensation or calibration.
local function create_income_actions(env)
    local api, owner, pending, failure, generation = {}, nil, false, nil, 0
    -- Every recurring gold source with a native percentage effect. Each adds to the
    -- game's own bonuses of that kind, so +100% is not double income. Looting and
    -- sacking are one-off rewards. The optional entries have no in-game verification
    -- yet: a rejected one must not take the verified effects down with it.
    local gold_effects = {
        {'wh_main_effect_economy_gdp_mod_all','faction_to_region_own_regions'},
        {'wh_main_effect_economy_trade_tariff_mod','faction_to_faction_own'},
        {'wh_main_effect_force_all_campaign_raid_income','faction_to_force_own',optional=true},
        {'wh_main_effect_modify_vassal_income','faction_to_faction_own',optional=true},
    }
    local function key(f, field) return 'wh3pt_' .. f:name() .. '_income_' .. field end
    local function log(text) (ModLog or out)('[WH3PT income] ' .. text) end
    local function faction()
        local f=env.faction()
        if owner and owner~=f:name() then
            if pending or cm:get_saved_value('wh3pt_'..owner..'_income_enabled')==true then
                cm:remove_effect_bundle('wh3pt_income',owner)
            end
            generation=generation+1;pending=false
        end
        owner=f:name()
        return f
    end
    local function find_bundle(f)
        local list=f:effect_bundles()
        for i=0,list:num_items()-1 do
            local b=list:item_at(i)
            if b:key()=='wh3pt_income' then return b end
        end
    end
    local function gold(f)
        local ok,value=pcall(function() return f:income() end)
        if ok and type(value)=='number' then return value end
    end
    -- The forecast settles a moment after the bundle does. Show what the engine
    -- now predicts, never a figure worked out here.
    local function report(token,before,percent,tries)
        cm:callback(function()
            if token~=generation then return end
            env.guard(function()
                local after=gold(env.faction())
                if before and after and after~=before then
                    if env.refresh_ui then env.refresh_ui() end
                    env.message('收入增幅 +'..percent..'%：预计每回合金币 '..before..' → '..after)
                elseif tries>1 then report(token,before,percent,tries-1) end
            end)
        end,0.5)
    end
    function api.pending() return pending end
    function api.failure() return failure end
    -- Saves from before 2.7.0 stored a multiplier: 3 meant today's +200%.
    function api.migrate()
        local f=faction()
        if cm:get_saved_value(key(f,'percent'))==true then return end
        local old=cm:get_saved_value(key(f,'value'))
        if type(old)=='number' then
            local percent=math.floor((old-1)*100+.5)
            cm:set_saved_value(key(f,'value'),percent)
            if percent<1 then cm:set_saved_value(key(f,'enabled'),false) end
        end
        cm:set_saved_value(key(f,'percent'),true)
    end
    function api.read()
        local f=faction()
        if cm:get_saved_value(key(f,'enabled'))~=true then return false,nil end
        return true,cm:get_saved_value(key(f,'value'))
    end
    function api.set(percent)
        assert(not cm:is_multiplayer(), '收入增幅仅支持单人战役')
        local f=faction()
        assert(percent==nil or (type(percent)=='number' and percent>=1 and percent<=10000), '收入增幅：请输入 1～10000%')
        generation=generation+1
        local token=generation
        pending=true;failure=nil
        local expected,before={},gold(f)
        local ok,err=pcall(function()
            if percent then
                local b=cm:create_new_custom_effect_bundle('wh3pt_income')
                assert(not b:is_null_interface(), '收入效果包不可用')
                b:set_duration(0)
                local function add(effect,scope,optional)
                    if b:add_effect(effect,scope,percent) then expected[effect..'/'..scope]=percent
                    elseif optional then log('已跳过游戏拒绝的可选效果：'..effect)
                    else error('游戏拒绝收入效果：'..effect) end
                end
                for _,spec in ipairs(gold_effects) do add(spec[1],spec[2],spec.optional) end
                for _,def in ipairs(env.resources or {}) do
                    if def.kind~='sigils' and def.income_effect and env.resource(def) then
                        add(def.income_effect,def.income_scope)
                    end
                end
                cm:apply_custom_effect_bundle_to_faction(b,f)
            else cm:remove_effect_bundle('wh3pt_income',f:name()) end
            cm:set_saved_value(key(f,'enabled'),percent~=nil)
            if percent then cm:set_saved_value(key(f,'value'),percent) end
        end)
        local function failed(message)
            cm:remove_effect_bundle('wh3pt_income',f:name())
            cm:set_saved_value(key(f,'enabled'),false)
            failure=tostring(message);pending=false
            log(failure)
            env.message('收入增幅已暂停：'..failure,true)
        end
        if not ok then failed(err);error(err) end
        -- One deferred bundle readback, never change values based on income predictions.
        cm:callback(function()
            if token~=generation or env.faction():name()~=f:name() then return end
            local valid,why=pcall(function()
                local b=find_bundle(f)
                if percent then
                    assert(b,'收入效果包未生效')
                    local effects=b:effects()
                    local found={}
                    for i=0,effects:num_items()-1 do
                        local e=effects:item_at(i)
                        found[e:key()..'/'..e:scope()]=e:value()
                    end
                    for k,v in pairs(expected) do assert(found[k] and math.abs(found[k]-v)<0.01,'收入效果回读不符：'..k) end
                else assert(not b,'收入效果未移除') end
            end)
            if not valid then failed(why) else pending=false end
            if env.refresh_ui then env.guard(env.refresh_ui) end
            if valid and percent then report(token,before,percent,3) end
        end,0.5)
    end
    function api.refresh()
        local enabled,percent=api.read()
        if enabled then api.set(percent) end
    end
    function api.start() env.guard(api.refresh) end
    return api
end
