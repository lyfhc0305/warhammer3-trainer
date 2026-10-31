-- Keep the native nine rituals, their payloads and one-time completion rules.
local function create_mortarch_actions(env)
    local api, pending = {}, false
    local owner = 'wh3_dlc29_nag_host_of_nagash'
    local bundle_key = 'wh3pt_mortarchs'
    local names = {'arkhan','mannfred','neferata','vlad','luthor','krell','dieter','walach','nameless'}
    local function key(name) return 'wh3_dlc29_nag_mortarchs_' .. name end
    local function setting(field) return 'wh3pt_' .. owner .. '_mortarchs_' .. field end
    function api.supported() return env.faction():name() == owner and type(nag_mortarchs_config) == 'table' end
    function api.enabled() return api.supported() and cm:get_saved_value(setting('enabled')) == true end
    function api.buildings_supported()
        return api.supported() and type(nag_mortarchs_config.locked_buildings_ritual) == 'table'
            and next(nag_mortarchs_config.locked_buildings_ritual) ~= nil
    end
    local function buildings_enabled()
        return api.buildings_supported() and cm:get_saved_value(setting('buildings_unlocked')) == true
    end
    local function unlock_buildings()
        assert(api.buildings_supported(), '当前阵营没有收服关联建筑')
        assert(type(cm.remove_event_restricted_building_record_for_faction) == 'function', '游戏不支持建筑解锁接口')
        local keys, seen = {}, {}
        -- Use the native ritual-to-building map, not all military buildings.
        for ritual, building in pairs(nag_mortarchs_config.locked_buildings_ritual) do
            assert(type(ritual) == 'string' and type(building) == 'string' and building ~= '', '收服建筑配置无效')
            if not seen[building] then keys[#keys + 1] = building; seen[building] = true end
        end
        table.sort(keys)
        for _, building in ipairs(keys) do
            cm:remove_event_restricted_building_record_for_faction(building, owner)
        end
        return #keys
    end
    function api.unlock_buildings()
        local count = unlock_buildings()
        -- One-time unlock command, like a resource grant: retain it across saves
        -- and do not relock buildings when the independent ritual toggle closes.
        cm:set_saved_value(setting('buildings_unlocked'), true)
        env.message('已执行 ' .. tostring(count) .. ' 项建筑前置解锁；请重开建筑列表，仍需正常建造')
    end
    local function sync()
        if not api.supported() then return end
        local f = env.faction()
        if not api.enabled() then return end
        local bundle = cm:create_new_custom_effect_bundle(bundle_key)
        assert(bundle and not bundle:is_null_interface(), '缺少死亡统帅修改数据')
        for _, name in ipairs(names) do
            local ritual = key(name)
            local status = f:rituals():ritual_status(ritual)
            assert(not status:is_null_interface(), '死亡统帅仪式不存在：' .. name)
            if cm:get_saved_value(setting('restore_' .. name)) == nil then
                cm:set_saved_value(setting('restore_' .. name), status:script_locked())
            end
            assert(bundle:add_effect('wh3_dlc29_nag_mortarch_ritual_unlock_' .. name, 'faction_to_faction_own', 1), '无法添加收服解锁效果')
            cm:unlock_ritual(f, ritual, 0)
        end
        -- Native costs accumulate +35% per subjugation; offset all nine uses.
        assert(bundle:add_effect('wh3_dlc29_effect_cost_mortarchs_subjugation', 'faction_to_faction_own', -1000), '无法添加收服费用效果')
        bundle:set_duration(0)
        cm:apply_custom_effect_bundle_to_faction(bundle, f)
        for _, name in ipairs(names) do
            assert(not f:rituals():ritual_status(key(name)):script_locked(), '收服条件解锁失败：' .. name)
        end
    end
    function api.schedule()
        if pending or (not api.enabled() and not buildings_enabled()) then return end
        pending = true
        env.callback(function()
            pending = false
            env.guard(function()
                if buildings_enabled() then unlock_buildings() end
                sync()
            end)
        end, 0.1)
    end
    function api.toggle(value)
        if value == false and not api.supported() then return end
        assert(api.supported(), '当前阵营没有死亡统帅收服机制')
        local enabled = value == nil and not api.enabled() or value == true
        cm:set_saved_value(setting('enabled'), enabled)
        if enabled then
            sync()
        else
            local f = env.faction()
            cm:remove_effect_bundle(bundle_key, owner)
            for _, name in ipairs(names) do
                local restore = cm:get_saved_value(setting('restore_' .. name))
                if restore ~= nil then
                    if restore then cm:lock_ritual(f, key(name)) else cm:unlock_ritual(f, key(name), 0) end
                    cm:set_saved_value(setting('restore_' .. name), nil)
                end
            end
        end
        env.message(enabled and '无条件收服已开：重新打开死亡统帅页面，选择人物后点击收服' or '无条件收服已关；已收服人物保留')
    end
    core:remove_listener('wh3pt_mortarch_mission')
    core:add_listener('wh3pt_mortarch_mission', 'MissionUpdated', true, function(c)
        env.guard(function()
            if not api.enabled() or c:faction():name() ~= owner then return end
            local mission = c:mission():mission_record_key()
            for _, name in ipairs(names) do
                if mission == key(name) then
                    -- Follow natural progress while enabled, so disabling cannot
                    -- relock a ritual whose actual requirements were completed.
                    cm:set_saved_value(setting('restore_' .. name), c:total_primary_objectives() - c:completed_primary_objectives() > 1)
                    api.schedule()
                    return
                end
            end
        end)
    end, true)
    for _, event in ipairs({'FactionTurnStart','PanelOpenedCampaign'}) do
        core:remove_listener('wh3pt_mortarch_' .. event)
        core:add_listener('wh3pt_mortarch_' .. event, event, true, function() env.guard(api.schedule) end, true)
    end
    api.schedule()
    return api
end
