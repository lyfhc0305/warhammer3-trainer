-- Campaign panel: model, commands and read-back. The UI lives in kit.lua and
-- views.lua, which build_ingame.py concatenates ahead of this file together
-- with CATALOG, STRATEGIC and UI_FONTS.
local PREFIX = 'wh3pt_'
-- UI identifiers have ASCII prefixes; keep byte slicing independent of the
-- native UTF-8 implementation and preserve localized suffixes intact.
local substring = string.sub_lua or string.sub
local definitions, command_defs, draft, cmd_draft, favourites, controls = {}, {}, {}, {}, {}, {}
-- Model state the views read: busy while a read-back is pending, the selected
-- character, the native recruitment pool and the status line.
local M = {busy = false, selected_cqi = nil, native_enabled = false, native_selected = {},
    status = '就绪：仅作用于当前玩家势力', status_error = false}
local faction_name
local render, create_ui, set_native_recruitment, reset_player_context, elite_tabs, views
local player_epoch = 0
local manual_battle
local target_actions
local mortarch_actions
local auto_upgrade
local income_actions
local resource_interface
local COMMANDS = {
    {id='treasury', label='国库资金注入', short='国库注入', unit='金', default=50000, step=10000, max=10000000, integer=true, btn='注入', hint='向国库追加资金'},
    {id='move_all', label='全军行动力恢复', short='全军行动力', unit='', default=1, step=1, max=1, integer=true, toggle=true, btn='恢复', hint='补满全部军队移动力'},
    {id='move_selected', label='选中角色行动力恢复', short='选中角色行动力', unit='', default=1, step=1, max=1, integer=true, toggle=true, btn='恢复', hint='补满选中领主或英雄'},
    {id='xp_selected', label='选中角色注入经验', short='选中角色经验', unit='XP', default=10000, step=5000, max=500000, integer=true, btn='注入', hint='为选中角色加经验'},
    {id='xp_all', label='全军将领注入经验', short='全军将领经验', unit='XP', default=10000, step=5000, max=500000, integer=true, btn='注入', hint='为在役将领加经验'},
    {id='unit_rank', label='全军兵牌等级', short='全军兵牌等级', unit='级', default=9, step=1, max=9, integer=true, btn='设置', hint='含精英，排除领主/英雄'},
    {id='free_skills', label='免技能点学习', short='免技能点学习', unit='', default=1, step=1, max=1, integer=true, toggle=true, btn='开关', hint='原生技能图标上右键学习'},
    {id='mortarchs', label='死亡统帅无条件收服', short='死亡统帅无条件收服', unit='', default=1, step=1, max=1, integer=true, toggle=true, btn='开关', hint='免前置和费用，原生页面收服'},
    {id='mortarch_buildings', label='收服关联建筑解锁', short='收服关联建筑解锁', unit='', default=1, step=1, max=1, integer=true, toggle=true, btn='解锁', hint='莱弥亚/血骑士，仍需正常建造'},
    {id='auto_upgrade', label='建筑自动升级', short='建筑自动升级', unit='', default=1, step=1, max=1, integer=true, toggle=true, btn='开关', hint='唯一升级选项且资源足够时，回合开始自动升级'},
    {id='preset_campaign', label='一键战役发育预设', short='战役发育预设', unit='', default=1, step=1, max=1, integer=true, toggle=true, preset=true, btn='启用', hint='秩序/发展/补员/移速/科研/建造'},
    {id='preset_battle', label='一键精锐战斗预设', short='精锐战斗预设', unit='', default=1, step=1, max=1, integer=true, toggle=true, preset=true, btn='启用', hint='特保/远抗/攻防/伤害/弹药/不溃'},
}

local CAMPAIGN_PRESET = {
    {'order', 50}, {'movement', 100}, {'diplomacy', 50}, {'recruitment', 10},
    {'upkeep', 50}, {'agent_player', 100}, {'growth', 500}, {'construction', 500},
    {'research', 3000}, {'foreign_replenishment', 100}, {'replenishment_cap', 100}, {'attrition', 1},
}

local BATTLE_PRESET = {
    {'ward', 33}, {'missile_resistance', 33}, {'attack', 50}, {'defence', 50},
    {'weapon', 50}, {'missile_damage', 50}, {'ammunition', 100}, {'armour', 100},
    {'reload', 100}, {'vigour', 1}, {'unbreakable', 1},
}

-- Features sit where they act, so nobody has to guess the page. A section is its
-- title followed by catalog or command ids; a test keeps this list complete.
local PAGES = {
    {name='总览', title='总览'},
    {name='内政', title='内政与经济', sections={
        {'资金与收入', 'treasury', 'income', 'upkeep'},
        {'建设与科技', 'growth', 'construction', 'auto_upgrade', 'research'},
        {'秩序与外交', 'order', 'diplomacy'},
    }},
    {name='军队', title='军队与将领', sections={
        {'行军', 'move_all', 'move_selected', 'movement', 'attrition'},
        {'招募与补员', 'recruitment', 'foreign_replenishment', 'replenishment_cap', 'unit_rank'},
        {'将领与英雄', 'xp_selected', 'xp_all', 'free_skills', 'agent_player'},
        {'阵营专属', 'mortarchs', 'mortarch_buildings'},
    }},
    {name='战斗', title='手动战斗加成', sections={
        {'恢复', 'healing', 'revive', 'ammo'},
        {'魔法', 'battle_magic', 'battle_cooldown'},
        {'敌方', 'battle_silence'},
        {'特性', 'vigour', 'unbreakable', 'vanguard', 'chainsword'},
        {'进攻', 'attack', 'weapon', 'missile_damage', 'range', 'reload', 'ammunition'},
        {'防御', 'defence', 'armour', 'ward', 'missile_resistance', 'health'},
        {'机动', 'speed', 'mass'},
    }},
    {name='精英', title='精英部队招募'},
}
for _, page in ipairs(PAGES) do PAGES[page.name] = page end

local function faction()
    if cm:is_multiplayer() then error('内置修改器仅支持单人战役') end
    local name = cm:get_local_faction_name(true)
    if not name or name == '' then error('尚未进入玩家战役') end
    local f = cm:get_faction(name)
    if not f or f:is_null_interface() or not f:is_human() then error('玩家势力不可用') end
    if faction_name ~= name then
        faction_name = name
        if reset_player_context then reset_player_context() end
    end
    return f
end

-- Cancel delayed work from an old player/campaign before it can use new state.
local function player_callback(fn, delay)
    local expected_name, expected_epoch = faction_name, player_epoch
    cm:callback(function()
        if cm:is_multiplayer() or expected_name ~= cm:get_local_faction_name(true)
            or expected_epoch ~= player_epoch then return end
        fn()
    end, delay)
end

local function setting(id, field)
    return PREFIX .. faction_name .. '_' .. id .. '_' .. field
end

-- Drafts the model changes must reach any input still on screen, or the next
-- render's capture writes the old text back over them.
local function set_draft(id, value)
    draft[id] = tostring(value)
    if views then views.sync_input(id) end
end

local function validate(def, raw)
    local n = tonumber(raw)
    if not n or not (n >= (def.min or 0) and n <= def.max) then
        error(def.label .. '：请输入 ' .. (def.min or 0) .. '～' .. def.max .. ' 的数值')
    end
    if def.integer and n % 1 ~= 0 then error(def.label .. '：必须是整数') end
    if def.choices and n < 1 then error('请选择有效档位') end
    return n
end

local function effect_spec(def, value)
    if def.effects then return def.effects[value], 1 end
    return def.effect, value * def.sign
end

-- Read model state, never turn saved UI preferences into a success indicator.
local function read_model(def)
    local list = faction():effect_bundles()
    for i = 0, list:num_items() - 1 do
        local bundle = list:item_at(i)
        if bundle:key() == PREFIX .. def.id then
            local effects = bundle:effects()
            for j = 0, effects:num_items() - 1 do
                local e = effects:item_at(j)
                if e:scope() == def.scope then
                    if def.effects then
                        for index, key in ipairs(def.effects) do
                            if e:key() == key and math.abs(e:value() - 1) < 0.001 then return true, index end
                        end
                    elseif e:key() == def.effect then return true, e:value() / def.sign end
                end
            end
            error(def.label .. '：已存在效果包，但数据无法识别')
        end
    end
    return false, nil
end

local function read(def)
    if def.income then return income_actions.read() end
    if def.manual_battle then return manual_battle.read(def) end
    return read_model(def)
end

local function active_items()
    local items = {}
    local ok_f = pcall(faction)
    if not ok_f then return items end
    for _, def in ipairs(CATALOG) do
        local ok, active, actual = pcall(read, def)
        if ok and active then
            local val_str = def.toggle and '保持生效'
                or (tostring(def.choices and def.choices[actual] or actual) .. def.unit)
            items[#items + 1] = {def = def, actual = actual, text = (def.manual_battle and '手动战斗 · ' or '') .. val_str}
        end
    end
    return items
end

local function message(text, is_error)
    M.status, M.status_error = text, is_error and true or false
    if views then views.show_status() end
end

-- Keep the footer readable: drop the chunk/line prefix and traceback (the full
-- error still goes to the mod log).
local function short_error(err)
    local text = tostring(err)
    local ok, cleaned = pcall(function()
        local first = string.match(text, '^([^\n]*)') or text
        return string.match(first, '^.-:%d+: (.+)$') or first
    end)
    return (ok and type(cleaned) == 'string' and cleaned ~= '') and cleaned or text
end

local function guarded(fn)
    local ok, err = xpcall(fn, debug.traceback)
    if not ok then
        local logger = type(ModLog) == 'function' and ModLog or out
        logger('[WH3PT] ' .. tostring(err))
        pcall(message, '失败：' .. short_error(err), true)
    end
    return ok
end

local function apply_model_bundle(f, def, value)
    cm:remove_effect_bundle(PREFIX .. def.id, faction_name)
    local b = cm:create_new_custom_effect_bundle(PREFIX .. def.id)
    if not b or b:is_null_interface() then error('缺少内置效果定义：' .. def.id) end
    local key, amount = effect_spec(def, value)
    if not b:add_effect(key, def.scope, amount) then error('游戏拒绝此效果：' .. key) end
    b:set_duration(0)
    cm:apply_custom_effect_bundle_to_faction(b, f)
end

manual_battle = create_manual_battle({catalog=CATALOG, faction=faction, setting=setting,
    validate=validate, read_model=read_model, apply_model=apply_model_bundle, guard=guarded})
-- Native pooled-resource income labels are event-driven. A custom effect
-- bundle does not emit their building/movement events, so rebind their own
-- native contexts after model validation. Never substitute calculated text.
local function refresh_native_income_ui()
    local income_logger = ModLog or out
    if type(find_uicomponent) ~= 'function' then return end
    local holder = find_uicomponent(core:get_ui_root(), 'resources_bar_holder')
    if not holder then return end
    local refreshed = 0
    local function visit(u, depth)
        if depth > 16 then return end
        local id = u:Id()
        if id == 'dy_income' or id == 'dy_projection' then
            local ok, err = pcall(function()
                local context_id = u:GetContextObjectId('CcoCampaignPooledResource')
                if not context_id or context_id == '' then return end
                local context = u:GetContextObject('CcoCampaignPooledResource')
                if not context then return end
                local key = context:Call('Key()')
                if type(key) ~= 'string' or key == '' then return end
                local before = u:GetStateText()
                u:SetContextObject(context)
                u:Layout()
                refreshed = refreshed + 1
                income_logger('[WH3PT income UI] ' .. key .. ': ' .. tostring(before) .. ' -> ' .. tostring(u:GetStateText()))
            end)
            if not ok then income_logger('[WH3PT income UI] refresh: ' .. tostring(err)) end
        end
        for i=0,u:ChildCount()-1 do visit(UIComponent(u:Find(i)),depth+1) end
    end
    visit(holder,0)
    income_logger('[WH3PT income UI] refreshed=' .. refreshed)
end

income_actions = create_income_actions({faction=faction, guard=guarded, message=message, refresh_ui=refresh_native_income_ui,
    resources=STRATEGIC.resources, resource=function(def) return resource_interface(def) end})

local function apply_single_bundle(f, def, value)
    if def.income then income_actions.set(value)
    elseif def.manual_battle then manual_battle.set(def, value) else apply_model_bundle(f, def, value) end
end

local function disable_feature(def)
    if def.income then income_actions.set(nil)
    elseif def.manual_battle then manual_battle.set(def, nil)
    else cm:remove_effect_bundle(PREFIX .. def.id, faction_name) end
end

local function apply(id, enabled, raw)
    if M.busy then error('请等待上一次操作回读完成') end
    local def = assert(definitions[id], '未知功能')
    local value = enabled and validate(def, raw) or nil
    local f = faction()
    if enabled and (id == 'healing' or id == 'revive') then
        local other = id == 'healing' and 'revive' or 'healing'
        if read(definitions[other]) then error('请先关闭另一项回血能力，避免叠加回血') end
    end
    if enabled then
        apply_single_bundle(f, def, value)
    else
        disable_feature(def)
    end
    if def.manual_battle then
        -- Pre-battle dialogs pause campaign timers. Preferences have already
        -- been written synchronously; waiting for cm:callback here freezes UI.
        local active, actual = read(def)
        if active ~= enabled or (enabled and actual ~= value) then error('手动战斗设置回读不一致') end
        if enabled then set_draft(id, value) end
        message(def.label .. (enabled and '：已保存，仅手动战斗生效' or '：已关闭'))
        if render then guarded(render) end
        return
    end
    M.busy = true
    message('等待游戏回读……')
    local function finish_apply()
        if def.income and income_actions.pending() then
            player_callback(finish_apply, 0.2)
            return
        end
        M.busy = false
        guarded(function()
            if def.income and income_actions.failure() then error(income_actions.failure()) end
            local active, actual = read(def)
            if active ~= enabled or (enabled and math.abs(actual - value) > 0.001) then
                error('效果包回读不一致，请刷新；未确认生效')
            end
            if enabled then
                set_draft(id, value)
                cm:set_saved_value(setting(id, 'value'), value)
            end
            message(def.label .. (enabled and (def.income and '：已应用，实际增幅以顶栏预测为准' or def.manual_battle and '：已保存，仅手动战斗生效' or '：已生效') or '：已关闭'))
        end)
        if render then guarded(render) end
    end
    player_callback(finish_apply, 0.1)
end

local function apply_preset(pairs_list, title)
    if M.busy then error('请等待上一次操作回读完成') end
    local f = faction()
    local targets, all_manual = {}, true
    for _, item in ipairs(pairs_list) do
        local def = definitions[item[1]]
        if def then
            local val = validate(def, item[2])
            apply_single_bundle(f, def, val)
            all_manual = all_manual and def.manual_battle == true
            targets[#targets + 1] = {def = def, val = val}
        end
    end
    M.busy = true
    message('正在应用' .. title .. '……')
    local function finish()
        M.busy = false
        guarded(function()
            for _, t in ipairs(targets) do
                local active, actual = read(t.def)
                if not active or math.abs(actual - t.val) > 0.001 then
                    error(t.def.label .. '：预设回读不一致')
                end
                set_draft(t.def.id, t.val)
                cm:set_saved_value(setting(t.def.id, 'value'), t.val)
            end
            message(title .. '已设置；部队加成仅手动战斗（' .. #targets .. ' 项）')
        end)
        if render then guarded(render) end
    end
    if all_manual then finish() else player_callback(finish, 0.1) end
end

local function clear_all()
    if M.busy then error('请等待上一次操作完成') end
    faction()
    if set_native_recruitment then set_native_recruitment(false, true) end
    if target_actions then target_actions.toggle(false) end
    if mortarch_actions then mortarch_actions.toggle(false) end
    if auto_upgrade then auto_upgrade.toggle(false) end
    for _, def in ipairs(CATALOG) do disable_feature(def) end
    M.busy = true
    player_callback(function()
        M.busy = false
        guarded(function()
            for _, def in ipairs(CATALOG) do
                if read(def) then error('仍有加成未移除：' .. def.label) end
            end
            message('全部修改已关闭；既有建筑、科技和伤亡不会回滚')
        end)
        if render then guarded(render) end
    end, 0.1)
end

local function collect_characters(f, prefer_selected)
    local chars = {}
    if not f.character_list then return chars, false end
    local list = f:character_list()
    if not list or not list.num_items then return chars, false end
    local matched_selected = nil
    for i = 0, list:num_items() - 1 do
        local ch = list:item_at(i)
        if ch and (not ch.is_null_interface or not ch:is_null_interface()) then
            local wounded = ch.is_wounded and ch:is_wounded() or false
            local garrison = ch.has_military_force and ch:has_military_force()
                and ch:military_force() and ch:military_force().is_armed_citizenry
                and ch:military_force():is_armed_citizenry() or false
            if not wounded and not garrison and ch.cqi then
                local cqi = ch:cqi()
                chars[#chars + 1] = cqi
                if prefer_selected and M.selected_cqi and cqi == M.selected_cqi then
                    matched_selected = cqi
                end
            end
        end
    end
    if prefer_selected then
        if matched_selected then return {matched_selected}, true end
        return {}, false
    end
    return chars, false
end

local function lookup_str(cqi)
    if cm.char_lookup_str then return cm:char_lookup_str(cqi) end
    return 'character_cqi:' .. tostring(cqi)
end

-- Strategic actions use live faction/army interfaces and confirm model changes.
local function strategic_name(def)
    if def.label and def.label ~= '' then return def.label end
    if effect and effect.get_localised_string then
        local ok, text = pcall(effect.get_localised_string, def.loc)
        if ok and text and text ~= '' and text ~= def.loc then return text end
    end
    return def.id
end
-- Mystic sigils are unspent initiative points, not a pooled resource balance.
-- Preserve allocated points and all native sources by adjusting our own bundle.
local function sigil_resource(f, def)
    if not f.points_limit_for_faction_initiative_set then return nil end
    local effect_key = 'wh3_dlc29_effect_initiative_set_point_increase_black_pyramid'
    local function integer(n) return type(n) == 'number' and n - n == 0 and n == math.floor(n) end
    local function read()
        local context = wh3pt_faction_context(f)
        local path = 'InitiativeManager().InitiativeSetList()'
        local count = context:Call(path .. '.Size()')
        assert(integer(count) and count >= 0 and count <= 10000, '符印列表读取失败')
        for i = 0, count - 1 do
            local entry = path .. '.At(' .. i .. ')'
            if context:Call(entry .. '.InitiativeSetContext().Key()') == def.id then
                local allocated = context:Call(entry .. '.PointsAllocated()')
                local capacity = f:points_limit_for_faction_initiative_set(def.id)
                assert(integer(allocated) and allocated >= 0 and integer(capacity) and capacity >= 0,
                    '符印数量读取失败')
                return capacity, allocated
            end
        end
        error('当前战役尚未提供黑色金字塔符印数据')
    end
    local function offset()
        local list = f:effect_bundles()
        for i = 0, list:num_items() - 1 do
            local bundle = list:item_at(i)
            if bundle:key() == 'wh3pt_sigils' then
                local effects = bundle:effects()
                for j = 0, effects:num_items() - 1 do
                    local e = effects:item_at(j)
                    if e:key() == effect_key then return e:value() end
                end
            end
        end
        return 0
    end
    local function apply(amount)
        local b = cm:create_new_custom_effect_bundle('wh3pt_sigils')
        assert(b and not b:is_null_interface(), '符印效果包不可用')
        b:set_duration(0)
        b:add_effect(effect_key, 'faction_to_faction_own', amount)
        cm:apply_custom_effect_bundle_to_faction(b, f)
    end
    return {
        value=function() local total, spent = read(); return math.max(0, total - spent) end,
        set_value=function(_, target)
            local total, spent = read()
            local previous = offset()
            apply(previous + spent + target - total)
            return function() apply(previous) end
        end
    }
end
resource_interface = function(def)
    local f = faction()
    if def.faction and def.faction ~= f:name() then return nil end
    if def.kind == 'sigils' then return sigil_resource(f, def) end
    local manager
    if def.region_group then
        local system = cm:model():world():region_group_pooled_resource_managers_system()
        manager = system:get_dynamic_manager_for_region_group_id(def.region_group)
    elseif f.pooled_resource_manager then
        manager = f:pooled_resource_manager()
    end
    if not manager or (manager.is_null_interface and manager:is_null_interface()) then return nil end
    local resource = manager:resource(def.id)
    if resource and not resource:is_null_interface() then return resource end
end
local function regiment_belongs_to_faction(def, f)
    local culture = f.subculture and f:subculture() or ''
    for _, rule in ipairs(def.eligibility or {}) do
        if (not rule.faction or rule.faction == f:name())
            and (not rule.subculture or rule.subculture == culture) then return true end
    end
    return false
end
local function set_resource(index, raw)
    if M.busy then error('请等待上一次操作回读完成') end
    local def = assert(STRATEGIC.resources[index], '未知资源')
    local resource = assert(resource_interface(def), '当前阵营没有此资源')
    local value = tonumber(raw)
    if not value or value ~= math.floor(value) or not (value >= def.min and value <= def.max) then
        error('请输入 ' .. def.min .. '～' .. def.max .. ' 的整数')
    end
    local before = resource:value()
    if value == before then message('资源已是目标值'); return end
    local rollback
    if def.kind == 'sigils' then
        rollback = resource:set_value(value)
    elseif value - before < def.factor_min or value - before > def.factor_max then
        error('此资源不支持该方向或幅度的修改')
    elseif def.region_group then
        cm:pooled_resource_factor_transaction(resource, def.factor, value - before)
    else
        cm:faction_add_pooled_resource(faction_name, def.id, def.factor, value - before)
    end
    M.busy = true
    player_callback(function()
        M.busy = false
        guarded(function()
            local ok, current = pcall(function() return assert(resource_interface(def), '资源已失效'):value() end)
            if not ok or current ~= value then
                if rollback then rollback() end
                if not ok then error(current) end
                error('游戏限制了资源修改：目标 ' .. value .. '，实际 ' .. current)
            end
            message(strategic_name(def) .. '：' .. before .. ' → ' .. current)
        end)
        if render then guarded(render) end
    end, 0.1)
end
-- Native recruitment owns army creation, payment, cards and detailed tooltips.
-- Only our isolated main-unit records are modified; vanilla pools remain intact.
local native_pending, native_revision = false, 0
local POOL_STOCK, POOL_SOURCE = 20, 'wh3_main_regiments_of_renown_pool'
local function pool_entries()
    local pool = faction():mercenary_pool()
    assert(pool and not pool:is_null_interface(), '当前势力没有可用的原生招募池')
    local list, result = pool:mercenary_pool_units(), {}
    for i = 0, list:num_items() - 1 do
        local entry = list:item_at(i)
        -- Adding a new group may invalidate every native pool entry interface.
        -- Finish all reads before any mutation; keep only immutable Lua values.
        result[entry:unit_record():key()] = {count=entry:unit_count(), max=entry:current_max_units()}
    end
    return result
end
local function native_wanted(def)
    return M.native_enabled and (regiment_belongs_to_faction(def, faction()) or M.native_selected[def.id] == true)
end
local function sync_native_pool()
    local f, entries, expected = faction(), pool_entries(), {}
    for _, def in ipairs(STRATEGIC.units) do
        local key, wanted = def.native_key, native_wanted(def)
        local entry = entries[key]
        if wanted or entry then
            local count = wanted and POOL_STOCK or 0
            if not entry or entry.count ~= count or entry.max ~= count then
                cm:add_unit_to_faction_mercenary_pool(f, key, POOL_SOURCE, count,
                    0, count, 0, faction_name, '', '', false, key)
            end
            expected[key] = count
        end
    end
    -- Native per-army RoR limit (including Nagash's 2-slot limit).
    if M.native_enabled then
        local bundle = cm:create_new_custom_effect_bundle(PREFIX .. 'native_ror')
        if not bundle or bundle:is_null_interface() then error('缺少精英上限效果定义，请更新修改包') end
        if not bundle:add_effect('wh3_main_effect_recruitment_cap_ror_units', 'faction_to_force_own', 9999) then
            error('游戏拒绝精英数量上限效果')
        end
        bundle:set_duration(0)
        cm:apply_custom_effect_bundle_to_faction(bundle, f)
    else
        cm:remove_effect_bundle(PREFIX .. 'native_ror', faction_name)
    end
    return expected
end
local function verify_native_pool(expected)
    local entries = pool_entries()
    for key, count in pairs(expected) do
        local entry = entries[key]
        if (not entry and count > 0) or (entry and (entry.count ~= count or entry.max ~= count)) then
            error('原生招募池回读不一致，请重新开启精英解锁')
        end
    end
    local bundles, cap_found = faction():effect_bundles(), false
    for i = 0, bundles:num_items() - 1 do
        local bundle = bundles:item_at(i)
        if bundle:key() == PREFIX .. 'native_ror' then
            local effects = bundle:effects()
            for j = 0, effects:num_items() - 1 do
                local e = effects:item_at(j)
                if e:key() == 'wh3_main_effect_recruitment_cap_ror_units'
                    and e:scope() == 'faction_to_force_own' and e:value() >= 9999 then cap_found = true end
            end
        end
    end
    if cap_found ~= M.native_enabled then error('精英军队数量上限效果未生效，请重试') end
end
local function native_recruitment_display()
    if type(find_uicomponent) ~= 'function' then return nil end
    return find_uicomponent(core:get_ui_root(), 'main_units_panel', 'recruitment_options', 'mercenary_display')
end
local function arrange_native_cards()
    -- Native recruitment rebuilds its cards when the pool changes. Never retain
    -- UIComponent handles across callbacks: pcall cannot catch stale C++ pointers.
    local display = native_recruitment_display()
    if not display or not display:Visible(true) then return end
    -- Collapse only matched pairs in this native list. Keep a marker ON the
    -- original so restoration never depends on a cached C++ UI handle.
    -- Off-screen cards still count: Visible(false) ignores scroll clipping.
    local by_key, budget = {}, 20000
    local known, cards, boxes, named, list_view = {}, {}, {}, {}, nil
    for _, def in ipairs(STRATEGIC.units) do known[def.id] = true; known[def.native_key] = true end
    local function walk(node, depth, entry, cell, in_view)
        if depth > 24 or budget <= 0 or named[cell] then return end
        budget = budget - 1
        local id = node:Id()
        if id == 'unit_listview_holder' then return end
        local card = entry or node
        local key, record = known[id] and id or nil, nil
        if not key then
            local ok, value = pcall(function() return node:GetContextObject('CcoMainUnitRecord'):Call('Key') end)
            if ok and type(value) == 'string' and value ~= '' then
                record = value
                if known[value] then key = value end
            end
        end
        if key and not by_key[key] then by_key[key] = card end
        -- Cells of the horizontal list are the cards; one is done once it has a name.
        if cell and (key or record) then
            named[cell] = true
            cards[#cards + 1] = {node = cell, key = key or record}
            return
        end
        if id == 'listview' then
            in_view = true
            list_view = list_view or node
        end
        local row = in_view and id == 'list_box'
        if row then boxes[#boxes + 1] = node end
        for i = 0, node:ChildCount() - 1 do
            local child = UIComponent(node:Find(i))
            if child then
                local next_entry = id == 'list_box' and child or entry
                walk(child, depth + 1, next_entry, row and child or cell, in_view)
            end
        end
    end
    walk(display, 0, nil, nil, false)
    elite_tabs.reveal(cards)
    for _, def in ipairs(STRATEGIC.units) do
        local unlocked = by_key[def.native_key]
        local original = by_key[def.id]
        if original and original ~= unlocked then
            local address = original:Find(PREFIX .. 'duplicate_hidden')
            local marker = address and UIComponent(address) or nil
            local replace = native_wanted(def) and unlocked and unlocked:Visible(false)
            if replace and original:Visible(false) then
                if not marker then
                    marker = UIComponent(original:CreateComponent(PREFIX .. 'duplicate_hidden', 'ui/wh3pt/label'))
                    marker:SetVisible(false)
                end
                marker:SetStateText('hidden')
                original:SetVisible(false)
            elseif not replace and marker and marker:GetStateText() == 'hidden' then
                original:SetVisible(true)
                marker:SetStateText('')
            end
        end
    end
    local found = display:Find('mercenary_recruitment_pool_list', false)
    elite_tabs.apply(found and UIComponent(found) or nil, list_view, boxes, cards, M.native_enabled)
end
local function schedule_native_sync(announce)
    if native_pending then return end
    native_pending = true
    player_callback(function()
        native_pending = false
        guarded(function()
            local expected = sync_native_pool()
            local revision = native_revision
            player_callback(function()
                if revision ~= native_revision then return end
                guarded(function()
                    verify_native_pool(expected)
                    arrange_native_cards()
                    if announce then message(M.native_enabled and '已解锁精英，重复兵牌合并显示；请重开精英招募框' or '精英解锁已关闭；已招募部队保留') end
                end)
                if render then guarded(render) end
            end, 0.1)
        end)
    end, 0.1)
end
set_native_recruitment = function(enabled, quiet)
    faction()
    native_revision = native_revision + 1
    M.native_enabled = enabled == true
    cm:set_saved_value(setting('native_ror', 'enabled'), M.native_enabled)
    schedule_native_sync(not quiet)
end
local function select_native_regiment(index)
    faction()
    native_revision = native_revision + 1
    local def = assert(STRATEGIC.units[index], '未知精英兵种')
    if regiment_belongs_to_faction(def, faction()) then
        set_native_recruitment(not M.native_enabled)
        return
    end
    M.native_selected[def.id] = not M.native_selected[def.id]
    cm:set_saved_value(setting('native_ror', def.id), M.native_selected[def.id])
    if M.native_selected[def.id] then M.native_enabled = true end
    cm:set_saved_value(setting('native_ror', 'enabled'), M.native_enabled)
    schedule_native_sync(true)
end

local function run_command_action(id, raw)
    if M.busy then error('请等待上一次操作回读完成') end
    local f = faction()
    local def = assert(command_defs[id], '未知指令：' .. tostring(id))
    if id == 'preset_campaign' then
        apply_preset(CAMPAIGN_PRESET, '战役发育预设')
        return
    elseif id == 'preset_battle' then
        apply_preset(BATTLE_PRESET, '精锐战斗预设')
        return
    elseif id == 'mortarchs' then
        mortarch_actions.toggle()
    elseif id == 'mortarch_buildings' then
        mortarch_actions.unlock_buildings()
    elseif id == 'auto_upgrade' then
        auto_upgrade.toggle()
    elseif id == 'free_skills' then
        target_actions.toggle()
    elseif id == 'unit_rank' then
        target_actions.rank(validate(def, raw or cmd_draft[id]))
    elseif id == 'treasury' then
        local amount = validate(def, raw or cmd_draft[id])
        if amount <= 0 then error('注入资金必须大于 0') end
        if not cm.treasury_mod then error('当前战役环境不支持国库修改接口') end
        cm:treasury_mod(faction_name, amount)
        cmd_draft[id] = tostring(amount)
        local now = f.treasury and f:treasury() or nil
        message('已注入 +' .. tostring(amount) .. ' 金' .. (now and ('（国库 ' .. tostring(now) .. '）') or ''))
    elseif id == 'move_all' or id == 'move_selected' then
        if not cm.replenish_action_points then error('当前战役环境不支持行动力恢复接口') end
        local cqis, used_sel = collect_characters(f, id == 'move_selected')
        if #cqis == 0 then
            error(id == 'move_selected' and '请先选中我方在役领主或英雄，再恢复行动力' or '当前势力没有可恢复行动力的在役角色')
        end
        for _, cqi in ipairs(cqis) do
            cm:replenish_action_points(lookup_str(cqi))
        end
        message(used_sel and '已补满选中角色移动力' or ('已补满 ' .. tostring(#cqis) .. ' 名在役角色移动力'))
    elseif id == 'xp_selected' or id == 'xp_all' then
        local amount = validate(def, raw or cmd_draft[id])
        if amount <= 0 then error('注入经验必须大于 0') end
        if not cm.add_agent_experience then error('当前战役环境不支持经验注入接口') end
        local cqis, used_sel = collect_characters(f, id == 'xp_selected')
        if #cqis == 0 then
            error(id == 'xp_selected' and '请先选中我方在役领主或英雄，再注入经验' or '当前势力没有可注入经验的在役角色')
        end
        for _, cqi in ipairs(cqis) do
            cm:add_agent_experience(lookup_str(cqi), amount)
        end
        cmd_draft[id] = tostring(amount)
        message(used_sel and ('已为选中角色 +' .. tostring(amount) .. ' 经验') or ('已为 ' .. tostring(#cqis) .. ' 名角色 +' .. tostring(amount) .. ' 经验'))
    end
    if render then guarded(render) end
end

-- ===========================================================================
-- Snapshots: what is in effect right now, so a preset or a clear can be undone.
-- ===========================================================================
local function snapshot()
    local snap = {features = {}, native = M.native_enabled,
        free_skills = target_actions and target_actions.enabled() or false,
        mortarchs = mortarch_actions and mortarch_actions.enabled() or false,
        auto_upgrade = auto_upgrade and auto_upgrade.enabled() or false}
    for _, def in ipairs(CATALOG) do
        local ok, active, actual = pcall(read, def)
        if ok and active then snap.features[def.id] = actual end
    end
    return snap
end

-- Income applies through its own deferred read-back; wait for it before judging.
local function when_settled(fn)
    local function poll()
        if income_actions.pending() then player_callback(poll, 0.2); return end
        fn()
    end
    player_callback(poll, 0.1)
end

local function restore(snap, title)
    if M.busy then error('请等待上一次操作回读完成') end
    local f, changed = faction(), {}
    for _, def in ipairs(CATALOG) do
        local want = snap.features[def.id]
        local ok, active, actual = pcall(read, def)
        if ok then
            if want ~= nil and (not active or math.abs((actual or 0) - want) > 0.001) then
                apply_single_bundle(f, def, want)
                changed[#changed + 1] = {def = def, val = want}
            elseif want == nil and active then
                disable_feature(def)
                changed[#changed + 1] = {def = def}
            end
        end
    end
    if snap.native ~= M.native_enabled then set_native_recruitment(snap.native, true) end
    if target_actions and snap.free_skills ~= target_actions.enabled() then target_actions.toggle(snap.free_skills) end
    if mortarch_actions and mortarch_actions.supported() and snap.mortarchs ~= mortarch_actions.enabled() then
        mortarch_actions.toggle(snap.mortarchs)
    end
    if auto_upgrade and snap.auto_upgrade ~= auto_upgrade.enabled() then auto_upgrade.toggle(snap.auto_upgrade) end
    M.busy = true
    message('正在撤销' .. title .. '……')
    when_settled(function()
        M.busy = false
        guarded(function()
            for _, c in ipairs(changed) do
                local active, actual = read(c.def)
                if c.val then
                    if not active or math.abs(actual - c.val) > 0.001 then error(c.def.label .. '：撤销回读不一致') end
                    set_draft(c.def.id, c.val)
                    cm:set_saved_value(setting(c.def.id, 'value'), c.val)
                elseif active then
                    error(c.def.label .. '：撤销后仍在生效')
                end
            end
            message('已撤销' .. title .. '，恢复 ' .. #changed .. ' 项')
        end)
        if render then guarded(render) end
    end)
end

-- ===========================================================================
-- UI wiring: the kit draws, the views decide what to draw, this file acts.
-- ===========================================================================
local K = create_kit({prefix = PREFIX, fonts = UI_FONTS, controls = controls, pages = #PAGES})
elite_tabs = create_elite_tabs({units = STRATEGIC.units, prefix = PREFIX, px = K.px, make = K.make})
views = create_views({
    prefix = PREFIX, kit = K, model = M, pages = PAGES, commands = COMMANDS, strategic = STRATEGIC,
    presets = {campaign = CAMPAIGN_PRESET, battle = BATTLE_PRESET},
    definitions = definitions, command_defs = command_defs, draft = draft, cmd_draft = cmd_draft,
    favourites = favourites, controls = controls,
    faction = faction, faction_name = function() return faction_name end, read = read, active_items = active_items,
    apply = apply, apply_preset = apply_preset, clear_all = clear_all, command = run_command_action,
    set_resource = set_resource, native_toggle = function(enabled) set_native_recruitment(enabled) end,
    native_select = select_native_regiment, elite_tabs = function() return elite_tabs end,
    arrange_native_cards = arrange_native_cards, targets = function() return target_actions end,
    mortarchs = function() return mortarch_actions end, auto_upgrade = function() return auto_upgrade end, income = income_actions,
    resource_interface = function(def) return resource_interface(def) end,
    regiment_belongs = regiment_belongs_to_faction, strategic_name = strategic_name,
    collect_characters = collect_characters, setting = setting, validate = validate,
    message = message, guarded = guarded, snapshot = snapshot, restore = restore,
})
render, create_ui = views.render, views.create_ui

local function is_toggle_shortcut(s)
    return type(s) == 'string' and (
        s == 'camera_bookmark_view0' or s == 'toggle_stream_pause'
        or s == 'script_F8' or s == 'F8' or s == 'F9'
    )
end

reset_player_context = function()
    player_epoch = player_epoch + 1
    manual_battle.migrate()
    income_actions.migrate()
    if target_actions then target_actions.reset() end
    if auto_upgrade then auto_upgrade.reset() end
    M.selected_cqi, M.busy, native_pending = nil, false, false
    native_revision = native_revision + 1
    for _, def in ipairs(CATALOG) do
        definitions[def.id] = def
        local saved = cm:get_saved_value(setting(def.id, 'value'))
        local ok, value = pcall(validate, def, saved)
        draft[def.id] = tostring(ok and value or def.default)
        favourites[def.id] = cm:get_saved_value(setting(def.id, 'favourite')) == true
    end
    for _, cmd in ipairs(COMMANDS) do
        command_defs[cmd.id] = cmd
        cmd_draft[cmd.id] = tostring(cmd.default)
        favourites[cmd.id] = cm:get_saved_value(setting(cmd.id, 'favourite')) == true
    end
    M.native_enabled = cm:get_saved_value(setting('native_ror', 'enabled')) == true
    M.native_selected = {}
    for _, def in ipairs(STRATEGIC.units) do
        M.native_selected[def.id] = cm:get_saved_value(setting('native_ror', def.id)) == true
    end
    M.status, M.status_error = '已匹配当前玩家阵营', false
    views.reset()
    if cm:get_saved_value(setting('native_ror', 'enabled')) ~= nil then schedule_native_sync(false) end
end

local function start()
    if cm:is_multiplayer() then out('[WH3PT] Disabled in multiplayer'); return end
    faction_name = cm:get_local_faction_name(true)
    faction()
    reset_player_context()
    manual_battle.start()
    income_actions.start()
    target_actions = create_target_actions({faction=faction, setting=setting, message=message,
        character=function() return M.selected_cqi and cm:get_character_by_cqi(M.selected_cqi) or nil end,
        callback=player_callback, guard=guarded, render=function() views.refresh() end})
    mortarch_actions = create_mortarch_actions({faction=faction, message=message, callback=player_callback, guard=guarded})
    auto_upgrade = create_auto_upgrade({faction=faction, setting=setting, message=message,
        callback=player_callback, guard=guarded, render=function() views.refresh() end})
    core:remove_listener('wh3pt_unit_card_select')
    core:remove_listener('wh3pt_free_skill_click')
    core:add_listener('wh3pt_free_skill_click', 'ComponentRClickUp', true, function(c)
        guarded(function() target_actions.skill(c.component) end)
    end, true)
    local saved_scale = cm:get_saved_value(PREFIX .. 'ui_scale')
    K.pref = 0
    for _, v in ipairs(K.UI_SCALE_STEPS) do
        if type(saved_scale) == 'number' and math.abs(saved_scale - v) < 0.001 then K.pref = v end
    end
    for _, event in ipairs({'UnitTrained', 'UnitCreated', 'RecruitmentItemIssuedByPlayer', 'FactionTurnStart', 'CharacterSelected', 'PanelOpenedCampaign'}) do
        local name = PREFIX .. 'native_' .. event
        core:remove_listener(name)
        core:add_listener(name, event, true, function()
            guarded(function()
                faction()
                if M.native_enabled then schedule_native_sync(false) end
            end)
        end, true)
    end
    core:remove_listener('wh3pt_native_recruit_click')
    core:add_listener('wh3pt_native_recruit_click', 'ComponentLClickUp', function(c)
        if not M.native_enabled or type(c.string) ~= 'string' or substring(c.string, 1, #PREFIX) == PREFIX then return false end
        local display = native_recruitment_display()
        return display and display:Visible(true)
    end, function() schedule_native_sync(false) end, true)
    core:remove_listener('wh3pt_click')
    core:add_listener('wh3pt_click', 'ComponentLClickUp', function(c)
        return type(c.string) == 'string' and substring(c.string, 1, #PREFIX) == PREFIX
    end, function(c)
        guarded(function()
            local id = substring(c.string, #PREFIX + 1)
            -- A drag ends in a click on the launcher; only a press that stayed put opens it.
            if id == 'open' and views.left_home(views.launcher(), 'launcher') then return end
            views.click(id)
        end)
    end, true)

    core:remove_listener('wh3pt_moved')
    core:add_listener('wh3pt_moved', 'ComponentMoved', function(c)
        return c.string == PREFIX .. 'panel' or c.string == PREFIX .. 'open'
    end, function(c)
        guarded(function()
            if c.string == PREFIX .. 'panel' then views.store_point(views.panel(), 'panel')
            else views.store_point(views.launcher(), 'launcher') end
        end)
    end, true)

    core:remove_listener('wh3pt_hotkey')
    core:add_listener('wh3pt_hotkey', 'ShortcutTriggered', function(c)
        return is_toggle_shortcut(c.string)
    end, function() guarded(function() views.click('open') end) end, true)

    core:remove_listener('wh3pt_char_select')
    core:add_listener('wh3pt_char_select', 'CharacterSelected', function(c)
        return c and c.character ~= nil
    end, function(c)
        guarded(function()
            faction()
            local ch = c:character()
            if ch and not ch:is_null_interface() and ch.faction and not ch:faction():is_null_interface()
                and ch:faction():name() == faction_name then
                M.selected_cqi = ch:cqi()
            else
                M.selected_cqi = nil
            end
            views.refresh()
        end)
    end, true)

    -- lib_campaign_ui clears its selection on the same two events. CharacterDeselected
    -- fires only when no other character takes over, so a switch keeps the new one.
    for _, event in ipairs({'CharacterDeselected', 'SettlementSelected'}) do
        local name = PREFIX .. 'clear_selection_' .. event
        core:remove_listener(name)
        core:add_listener(name, event, true, function()
            guarded(function()
                M.selected_cqi = nil
                views.refresh()
            end)
        end, true)
    end

    -- A Movie pack keeps loading after a game patch, so say so in the panel. Anything
    -- that is not a plain x.y.z version is ignored rather than guessed at.
    local build = STRATEGIC.build
    if build and build.game ~= '' and common and common.get_context_value then
        local ok, number = pcall(common.get_context_value, 'CcoGameCore', '', 'BuildNumberShort()')
        local running = ok and type(number) == 'string' and string.match(number, '%d+%.%d+%.%d+')
        if running and running ~= build.game then
            message('游戏已更新到 ' .. running .. '，请运行安装器重新适配', true)
        end
    end

    -- Last: a failed first render must still leave the launcher and F9 handlers alive.
    create_ui()
end

cm:add_first_tick_callback(function() guarded(start) end)
-- Returned only for the offline Lua harness; no global game functions are patched.
return {
    manual=manual_battle, income=income_actions, read_model=read_model,
    targets=function() return target_actions end, auto_upgrade=function() return auto_upgrade end,
    start=start, read=read, apply=apply, clear=clear_all, validate=validate,
    strategic=STRATEGIC, set_resource=set_resource, native_toggle=set_native_recruitment,
    native_select=select_native_regiment, native_sync=schedule_native_sync, native_filter=arrange_native_cards,
    elite_tabs=function() return elite_tabs end,
    regiment_belongs=regiment_belongs_to_faction, resource_interface=resource_interface,
    click=function(id) return views.click(id) end, command=run_command_action, catalog=CATALOG, commands=COMMANDS, pages=PAGES,
    snapshot=snapshot, restore=restore, views=views, model=M,
    layout=function() return views.layout_info() end,
}
