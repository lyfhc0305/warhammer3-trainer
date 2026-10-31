-- Scale-robust drawer UI for Total War: Warhammer III (native ink, copper & gold).
-- CATALOG and UI_FONTS are injected by build_ingame.py.
local PREFIX = 'wh3pt_'
-- WH3 replaces string.find with a three-argument UTF-8 function. The original
-- Lua implementation retains literal-search support through string.find_lua.
local literal_find = string.find_lua or string.find
-- UI identifiers have ASCII prefixes; keep byte slicing independent of the
-- native UTF-8 implementation and preserve localized suffixes intact.
local substring = string.sub_lua or string.sub
local definitions, command_defs, draft, cmd_draft, favourites, controls = {}, {}, {}, {}, {}, {}
local panel, launcher, search, status_ui, status_dot_ui, list_ui
local group, query, search_text, visible, collapsed, busy = '总览', '', '', false, false, false
local active_sheet, only_favourites = false, false
local current_rows, faction_name, selected_cqi = {}, nil, nil
local status, status_error = '就绪：仅作用于当前玩家势力', false
local render, create_ui, refit, set_native_recruitment, reset_player_context, elite_tabs
local player_epoch = 0
local manual_battle
local target_actions
local mortarch_actions
local income_actions
local resource_interface
local rows_parent, overlay_parent, title_ui, rail_count_ui

-- label keeps the long form for validation messages; short/hint are display text.
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
        {'建设与科技', 'growth', 'construction', 'research'},
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
        {'特性', 'vigour', 'unbreakable', 'vanguard', 'chainsword'},
        {'进攻', 'attack', 'weapon', 'missile_damage', 'range', 'reload', 'ammunition'},
        {'防御', 'defence', 'armour', 'ward', 'missile_resistance', 'health'},
        {'机动', 'speed', 'mass'},
    }},
    {name='精英', title='精英部队招募'},
    {name='全部', title='全部功能'},
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
    status, status_error = text, is_error and true or false
    if status_ui and refit then refit(status_ui, text) end
    if status_dot_ui and status_dot_ui.SetState then
        pcall(function() status_dot_ui:SetState(status_error and 'danger_dot' or 'ok_dot') end)
    end
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
    if busy then error('请等待上一次操作回读完成') end
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
        if enabled then draft[id] = tostring(value) end
        message(def.label .. (enabled and '：已保存，仅手动战斗生效' or '：已关闭'))
        if render then guarded(render) end
        return
    end
    busy = true
    message('等待游戏回读……')
    local function finish_apply()
        if def.income and income_actions.pending() then
            player_callback(finish_apply, 0.2)
            return
        end
        busy = false
        guarded(function()
            if def.income and income_actions.failure() then error(income_actions.failure()) end
            local active, actual = read(def)
            if active ~= enabled or (enabled and math.abs(actual - value) > 0.001) then
                error('效果包回读不一致，请刷新；未确认生效')
            end
            if enabled then
                draft[id] = tostring(value)
                cm:set_saved_value(setting(id, 'value'), value)
            end
            message(def.label .. (enabled and (def.income and '：已应用，实际增幅以顶栏预测为准' or def.manual_battle and '：已保存，仅手动战斗生效' or '：已生效') or '：已关闭'))
        end)
        if render then guarded(render) end
    end
    player_callback(finish_apply, 0.1)
end

local function apply_preset(pairs_list, title)
    if busy then error('请等待上一次操作回读完成') end
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
    busy = true
    message('正在应用' .. title .. '……')
    local function finish()
        busy = false
        guarded(function()
            for _, t in ipairs(targets) do
                local active, actual = read(t.def)
                if not active or math.abs(actual - t.val) > 0.001 then
                    error(t.def.label .. '：预设回读不一致')
                end
                draft[t.def.id] = tostring(t.val)
                cm:set_saved_value(setting(t.def.id, 'value'), t.val)
            end
            message(title .. '已设置；部队加成仅手动战斗（' .. #targets .. ' 项）')
        end)
        if render then guarded(render) end
    end
    if all_manual then finish() else player_callback(finish, 0.1) end
end

local function clear_all()
    if busy then error('请等待上一次操作完成') end
    faction()
    if set_native_recruitment then set_native_recruitment(false, true) end
    if target_actions then target_actions.toggle(false) end
    if mortarch_actions then mortarch_actions.toggle(false) end
    for _, def in ipairs(CATALOG) do disable_feature(def) end
    busy = true
    player_callback(function()
        busy = false
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
                if prefer_selected and selected_cqi and cqi == selected_cqi then
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
local strategic_page, own_regiments, home_resource_page = 1, true, 1
local strategic_category, category_menu = 'all', false
local strategic_sort = 'recruitment_cost'
local strategic_inputs, strategic_draft = {}, {}
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
    if busy then error('请等待上一次操作回读完成') end
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
    busy = true
    player_callback(function()
        busy = false
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
local native_enabled, native_selected, native_pending, native_revision = false, {}, false, 0
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
    return native_enabled and (regiment_belongs_to_faction(def, faction()) or native_selected[def.id] == true)
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
    if native_enabled then
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
    if cap_found ~= native_enabled then error('精英军队数量上限效果未生效，请重试') end
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
    elite_tabs.apply(found and UIComponent(found) or nil, list_view, boxes, cards, native_enabled)
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
                    if announce then message(native_enabled and '已解锁精英，重复兵牌合并显示；请重开精英招募框' or '精英解锁已关闭；已招募部队保留') end
                end)
                if render then guarded(render) end
            end, 0.1)
        end)
    end, 0.1)
end
set_native_recruitment = function(enabled, quiet)
    faction()
    native_revision = native_revision + 1
    native_enabled = enabled == true
    cm:set_saved_value(setting('native_ror', 'enabled'), native_enabled)
    schedule_native_sync(not quiet)
end
local function select_native_regiment(index)
    faction()
    native_revision = native_revision + 1
    local def = assert(STRATEGIC.units[index], '未知精英兵种')
    if regiment_belongs_to_faction(def, faction()) then
        set_native_recruitment(not native_enabled)
        return
    end
    native_selected[def.id] = not native_selected[def.id]
    cm:set_saved_value(setting('native_ror', def.id), native_selected[def.id])
    if native_selected[def.id] then native_enabled = true end
    cm:set_saved_value(setting('native_ror', 'enabled'), native_enabled)
    schedule_native_sync(true)
end

local function run_command_action(id, raw)
    if busy then error('请等待上一次操作回读完成') end
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
-- Scale-robust layout
-- ===========================================================================
-- Every coordinate passed to component()/label()/button()/card() is a design
-- unit: the layout at 100% UI scale, relative to the panel origin. px()
-- multiplies by S, which is calibrated in compute_scale() at create_ui() time.
local DESIGN_W, DESIGN_H = 520, 640
local RAIL_W = 68
local CX = RAIL_W + 12              -- content column left edge
local CW = DESIGN_W - CX - 14       -- content column width
local PAGE_Y, LIST_Y = 62, 102      -- first content row without / with the page strip
local FOOTER_Y = DESIGN_H - 46      -- footer divider
local CONTENT_BOTTOM = FOOTER_Y - 8
local FRAME_INSET = 4               -- thickness of the panel frame texture
local ROW_H, ROW_GAP, SECTION_H = 52, 4, 30
local ROW_W = CW - 18               -- list rows; the rest is the native scrollbar
local RAIL_H = 56 + (#PAGES + 1) * 36 + 92   -- height of the collapsed panel
local DRAG_SLOP = 6                 -- screen pixels a press may wander and stay a click
local LAUNCHER_W, LAUNCHER_H, LAUNCHER_GAP = 116, 30, 8
local LAUNCHER_CLEARANCE = 56       -- kept free left of the native tab strip
local EDGE_RIGHT, EDGE_TOP, EDGE_BOTTOM = 16, 16, 12   -- screen pixels
local S_MIN, S_MAX = 1.0, 2.0
-- Calibration reference: ten CJK glyphs in the 14px 'probe' label state. CJK
-- glyphs are full-width (advance ~= 1 em), so at 100% UI scale the reference
-- should measure ~10 * 14 = 140 units. The ratio measured/expected is the
-- effective text scale.
local PROBE_TEXT, PROBE_CHARS, PROBE_FONT = '战锤修改器界面校准用', 10, 14
local UI_SCALE_STEPS = {0, 1.0, 1.25, 1.5, 1.75, 2.0}   -- 0 = automatic
local FONTS = type(UI_FONTS) == 'table' and UI_FONTS or {}
local S, TS, ox, oy = 1, 1, 0, 0
local ui_scale_pref, ui_info = 0, {}
-- Per-component layout metadata (kind, base state, size) used by text fitting.
local meta = setmetatable({}, {__mode = 'k'})

local function px(v) return math.floor(v * S + 0.5) end
local function clamp(v, lo, hi) return math.max(lo, math.min(hi, v)) end

-- ---------------------------------------------------------------------------
-- UTF-8 helpers. WH3 replaces some string functions with UTF-8 variants, so
-- byte access is only trusted after a self-test; otherwise fall back to a
-- gmatch split that is verified by re-concatenation, otherwise no truncation.
-- ---------------------------------------------------------------------------
local byte_fn = string.byte_lua or string.byte
local BYTES_OK = (function()
    local ok, res = pcall(function()
        local s = '\228\184\173a'
        return #s == 4 and byte_fn(s, 1) == 228 and byte_fn(s, 2) == 184 and byte_fn(s, 4) == 97
            and substring(s, 4, 4) == 'a' and substring(s, 1, 3) == '\228\184\173'
    end)
    return ok and res == true
end)()

local function utf8_chars(text)
    local chars = {}
    if BYTES_OK then
        local i, n = 1, #text
        while i <= n do
            local c = byte_fn(text, i)
            local len = (c < 0x80 and 1) or (c >= 0xF0 and 4) or (c >= 0xE0 and 3) or (c >= 0xC0 and 2) or 1
            chars[#chars + 1] = substring(text, i, math.min(n, i + len - 1))
            i = i + len
        end
        return chars
    end
    local ok = pcall(function()
        for ch in string.gmatch(text, '[%z\1-\127\192-\253][\128-\191]*') do chars[#chars + 1] = ch end
    end)
    if ok and table.concat(chars) == text then return chars end
    return nil
end

-- Fallback width estimate when TextDimensions is unavailable: CJK ~1 em,
-- 2-byte symbols ~0.7 em, ASCII ~0.62 em, multiplied by the text scale TS.
local function estimate_width(text, font_px)
    local chars, units = utf8_chars(text), 0
    if chars then
        for _, ch in ipairs(chars) do
            local n = #ch
            units = units + ((n >= 3 and 1.0) or (n == 2 and 0.7) or 0.62)
        end
    else
        units = #text * 0.62
    end
    return units * font_px * TS
end

local function font_of(kind, state)
    local t = FONTS[kind]
    return (t and t[state]) or 13
end

-- Optional native measurement; returns nil when missing or unusable.
local function measure(u)
    local fn = u.TextDimensions
    if type(fn) ~= 'function' then return nil end
    local ok, w, h, lines = pcall(fn, u)
    if ok and type(w) == 'number' and w > 0 then
        return w, (type(h) == 'number' and h or nil), (type(lines) == 'number' and lines or nil)
    end
    return nil
end

local function set_state(u, state)
    if state and u.SetState then pcall(function() u:SetState(state) end) end
end

local function tooltip(u, text)
    if u.SetTooltipText then pcall(function() u:SetTooltipText(text, true) end) end
end

-- Returns width_ok, height_ok, measured_width for the text currently set.
-- TextDimensions reports the laid-out block, so a wrapped line measures narrow
-- but tall; the template forbids wrapping and this catches it if that ever fails.
local function check(u, m, state, text, inner_w)
    local w, h, lines = measure(u)
    if not w then
        w = estimate_width(text, font_of(m.kind, state))
        return w <= inner_w, true, w
    end
    local one_line = (not lines or lines <= 1) and (not h or h < font_of(m.kind, state) * TS * 2)
    return w <= inner_w and one_line, (not h or h <= m.h + 1), w
end

-- Set text so it never exceeds the component: base state, then the *_sm
-- smaller-font state, then UTF-8 safe truncation with an ellipsis.
local function fit_text(u, text)
    text = tostring(text == nil and '' or text)
    local m = meta[u]
    if not m then u:SetStateText(text); return 0 end
    local candidates = {m.base}
    local t = FONTS[m.kind]
    if m.base and t and t[m.base .. '_sm'] then candidates[2] = m.base .. '_sm' end
    local inner_w = math.max(1, m.w - 2 * px(m.kind == 'button' and 4 or 1))
    local state, width = m.base, 0
    for i, st in ipairs(candidates) do
        -- Always (re)apply: the engine may have moved the button to hover/down.
        set_state(u, st); m.cur = st
        u:SetStateText(text)
        if text == '' then m.text_w = 0; return 0 end
        local w_ok, h_ok, w = check(u, m, st, text, inner_w)
        state, width = st, w
        if w_ok and (h_ok or i == #candidates) then m.text_w = w; return w end
    end
    local chars = utf8_chars(text)
    if not chars or #chars < 2 then m.text_w = width; return width end
    local lo, hi, best = 0, #chars - 1, 0
    while lo <= hi do
        local mid = math.floor((lo + hi) / 2)
        local candidate = table.concat(chars, '', 1, mid) .. '…'
        u:SetStateText(candidate)
        local w_ok, _, w = check(u, m, state, candidate, inner_w)
        if w_ok then best, width, lo = mid, w, mid + 1 else hi = mid - 1 end
    end
    local shown = table.concat(chars, '', 1, best) .. '…'
    u:SetStateText(shown)
    if best == 0 then local _, _, w = check(u, m, state, shown, inner_w); width = w end
    tooltip(u, text)
    m.text_w = width
    return width
end

refit = function(u, text, state)
    local m = meta[u]
    if m then
        if state then m.base = state end
        return fit_text(u, text)
    end
    set_state(u, state)
    u:SetStateText(tostring(text))
    return 0
end

-- Text width in design units (after fitting).
local function text_w(u)
    local m = meta[u]
    return (m and m.text_w or 0) / S
end

-- Shrink a left-aligned label to its fitted text so a neighbour can follow it.
local function shrink_to_text(u)
    local m = meta[u]
    if not m then return 0 end
    if m.text_w and m.text_w > 0 then
        local w = math.min(m.w, math.ceil(m.text_w) + 2 * px(6))
        if w < m.w then u:Resize(w, m.h); m.w = w end
    end
    return m.w / S
end

-- Same for a right-aligned label: keep the right edge, move the left edge.
local function shrink_right(u)
    local m = meta[u]
    if not m then return 0 end
    if m.text_w and m.text_w > 0 then
        local w = math.min(m.w, math.ceil(m.text_w) + 2 * px(6))
        if w < m.w then
            local x, y = u:Position()
            u:MoveTo(x + m.w - w, y); u:Resize(w, m.h); m.w = w
        end
    end
    return m.w / S
end

-- Low level constructor in screen pixels.
local function make(parent, id, template, kind, x, y, w, h, text, state)
    local u = UIComponent(parent:CreateComponent(PREFIX .. id, template))
    u:SetDockingPoint(0)
    if u.SetDockOffset then u:SetDockOffset(0, 0) end
    u:SetCanResizeWidth(true); u:SetCanResizeHeight(true); u:Resize(w, h)
    set_state(u, state)
    if template == 'ui/common ui/text_box' and u.ResizeCurrentStateImage then
        pcall(function() u:ResizeCurrentStateImage(0, w, h) end)
    end
    u:MoveTo(x, y)
    if kind then meta[u] = {kind = kind, base = state, cur = state, w = w, h = h} end
    if text ~= nil then
        if kind then fit_text(u, text) else u:SetStateText(tostring(text)) end
    end
    u:PropagatePriority(200)
    return u
end

elite_tabs = create_elite_tabs({units=STRATEGIC.units, prefix=PREFIX, px=px, make=make})

-- Design-unit constructor relative to the panel origin. Edges are rounded,
-- so a child whose design rect is inside the panel stays inside in pixels.
local function component(parent, id, template, kind, dx, dy, dw, dh, text, state)
    local x0, y0 = px(dx), px(dy)
    local w = math.max(1, px(dx + dw) - x0)
    local h = math.max(1, px(dy + dh) - y0)
    return make(parent, id, template, kind, ox + x0, oy + y0, w, h, text, state)
end

local function label(parent, id, dx, dy, dw, dh, text, state)
    return component(parent, id, 'ui/wh3pt/label', 'label', dx, dy, dw, dh, text, state or 'active')
end

local function card(parent, id, dx, dy, dw, dh, text, state)
    return component(parent, id, 'ui/wh3pt/card', 'card', dx, dy, dw, dh, text or '', state or 'active')
end

local function button(parent, id, dx, dy, dw, dh, text, state)
    local u = component(parent, id, 'ui/wh3pt/button', 'button', dx, dy, dw, dh, text, state or 'active')
    controls[PREFIX .. id] = u
    return u
end

local function input(parent, id, dx, dy, dw, dh, text)
    return component(parent, id, 'ui/common ui/text_box', nil, dx, dy, dw, dh, text)
end

-- ---------------------------------------------------------------------------
-- Scale calibration
-- ---------------------------------------------------------------------------
-- 1. root:Dimensions() is the coordinate space MoveTo/Resize use; it is also
--    the clamp for the panel. core:get_screen_resolution() (optional) is only
--    compared against it: if root is smaller than the physical resolution the
--    engine already scales geometry, the probe then measures ~100% in root
--    units and S stays ~1, so nothing is scaled twice.
-- 2. If TextDimensions exists, a probe label measures PROBE_TEXT; the ratio to
--    the 100% expectation is the text scale (clamped to [S_MIN, S_MAX]).
-- 3. Without TextDimensions the scale is unknown: S = 1 unless the user picked
--    a manual scale with the header '缩放' button (saved per campaign).
local function calibrate(root, rw, rh)
    local info = {root_w = rw, root_h = rh}
    local ok_res, res_w, res_h = pcall(function()
        if core and type(core.get_screen_resolution) == 'function' then return core:get_screen_resolution() end
    end)
    if ok_res and type(res_w) == 'number' and res_w > 0 then
        info.res_w, info.res_h = res_w, res_h
        info.space_ratio = res_w / math.max(1, rw)
    end
    pcall(function()
        local p = UIComponent(root:CreateComponent(PREFIX .. 'probe', 'ui/wh3pt/label'))
        local ok_m, w = pcall(function()
            p:SetCanResizeWidth(true); p:SetCanResizeHeight(true)
            p:Resize(math.max(800, rw), 80)  -- wide enough that the reference never wraps
            set_state(p, 'probe')
            p:SetStateText(PROBE_TEXT)
            return measure(p)
        end)
        p:Destroy()
        if ok_m and w then info.probe_w = w end
    end)
    if info.probe_w then info.text_ratio = info.probe_w / (PROBE_CHARS * PROBE_FONT) end
    return info
end

local function compute_scale(root)
    local rw, rh = root:Dimensions()
    local info = calibrate(root, rw, rh)
    local auto = 1
    if info.text_ratio then auto = clamp(math.floor(info.text_ratio * 20 + 0.5) / 20, S_MIN, S_MAX) end
    local wanted = ui_scale_pref > 0 and ui_scale_pref or auto
    local fit_w = (rw - 2 * EDGE_RIGHT) / DESIGN_W
    local fit_h = (rh - EDGE_TOP - EDGE_BOTTOM) / (LAUNCHER_H + LAUNCHER_GAP + DESIGN_H)
    -- Clamped to the screen: geometry shrinks uniformly and fit_text() steps
    -- fonts down / truncates so nothing overflows.
    S = math.max(0.5, math.min(wanted, fit_w, fit_h))
    TS = info.text_ratio and math.max(0.5, info.text_ratio) or wanted
    info.auto, info.wanted, info.scale, info.text_scale = auto, wanted, S, TS
    info.clamped = S < wanted - 0.001
    ui_info = info
    return rw, rh
end

local function scale_label()
    if ui_scale_pref > 0 then return '缩放 ' .. tostring(math.floor(ui_scale_pref * 100 + 0.5)) .. '%' end
    return '缩放 自动'
end

-- ---------------------------------------------------------------------------
-- Drawing
-- ---------------------------------------------------------------------------
local function capture()
    for key, u in pairs(strategic_inputs) do
        strategic_draft[key] = u:GetStateText()
        local index = tonumber(string.match(key, '^resource_(%d+)$'))
        local def = index and STRATEGIC.resources[index]
        if def then cm:set_saved_value(setting('resource_' .. def.id, 'target'), strategic_draft[key]) end
    end
    if search then search_text = search:GetStateText() end
    for _, row in ipairs(current_rows) do
        if row.input then
            if row.is_cmd then
                cmd_draft[row.def.id] = row.input:GetStateText()
            else
                draft[row.def.id] = row.input:GetStateText()
            end
        end
    end
end

local function group_digits(n)
    local s = tostring(math.floor((tonumber(n) or 0) + 0.5))
    local neg = substring(s, 1, 1) == '-'
    if neg then s = substring(s, 2) end
    local tail = ''
    while #s > 3 do
        tail = ',' .. substring(s, #s - 2) .. tail
        s = substring(s, 1, #s - 3)
    end
    return (neg and '-' or '') .. s .. tail
end

local function row_detail(def, ok)
    if not ok then return '读取失败，请重新打开面板' end
    if def.hint and def.hint ~= '' then return def.hint end
    return '范围 ' .. tostring(def.min or 0) .. '～' .. tostring(def.max) .. def.unit
end

local function section(parent, id, dy, title, right_text)
    label(parent, 'sec_' .. id, CX, dy, CW - 130, 24, title, 'section')
    if right_text then label(parent, 'secr_' .. id, CX + CW - 130, dy, 130, 24, right_text, 'quiet_right') end
    card(parent, 'div_' .. id, CX, dy + 25, CW, 1, '', 'divider')
end

local function active_row(parent, prefix, it, x, w, ry)
    local id = it.def.id
    local split = math.floor(w * 0.5)
    card(parent, prefix .. '_row_' .. id, x, ry, w, 32, '', 'active')
    label(parent, prefix .. '_name_' .. id, x + 12, ry + 4, split - 12, 24, it.def.label, 'body')
    label(parent, prefix .. '_val_' .. id, x + split, ry + 4, w - split - 48, 24, it.text, 'accent_right')
    button(parent, 'off_' .. id, x + w - 34, ry + 3, 30, 26, '×', 'ghost_card')
end

local function draw_search(parent, with_favourites)
    local extra = with_favourites and 38 or 0
    local sw = CW - 56 - 6 - 32 - 6 - extra
    search = input(parent, 'search', CX, 60, sw, 32, search_text)
    button(parent, 'search_go', CX + sw + 6, 60, 56, 32, '搜索', 'active')
    button(parent, 'search_clear', CX + CW - 32 - extra, 60, 32, 32, '×', query ~= '' and 'active' or 'ghost_page')
    if with_favourites then
        button(parent, 'favourites_only', CX + CW - 32, 60, 32, 32, only_favourites and '★' or '☆',
            only_favourites and 'selected' or 'active')
    end
end

local function selected_name()
    local ok, name = pcall(function()
        local ch = cm:get_character_by_cqi(selected_cqi)
        local surname = ch:get_surname()
        return common.get_localised_string(ch:get_forename())
            .. (surname ~= '' and (' ' .. common.get_localised_string(surname)) or '')
    end)
    return ok and name ~= '' and name or ('#' .. tostring(selected_cqi))
end

-- One strip of context above a page: what the page acts on right now.
local function draw_strip(parent)
    if group == '全部' then return draw_search(parent, true) end
    local main, sub, lit = '', '', false
    local ok_f, f = pcall(faction)
    if group == '军队' then
        local count = tostring(#(ok_f and collect_characters(f, false) or {}))
        lit = selected_cqi ~= nil
        main = (lit and ('已选中 ' .. selected_name()) or '未选中角色') .. ' · 在役将领 ' .. count .. ' 名'
        sub = target_actions and target_actions.summary() or '请先选中我方军队'
    elseif group == '内政' then
        main = '国库 ' .. ((ok_f and f.treasury) and (group_digits(f:treasury()) .. ' 金') or '—')
        local ok_i, income = pcall(function() return f:income() end)
        sub = ok_i and type(income) == 'number' and ('预计每回合收入 ' .. group_digits(income) .. ' 金') or '预计收入以顶栏为准'
    else
        main, sub = '全部只在手动战斗生效', '战役地图上只保存设置；不影响战前预测和自动结算'
    end
    card(parent, 'strip', CX, 60, CW, 38, '', lit and 'accent' or 'active')
    label(parent, 'strip_main', CX + 14, 62, CW - 28, 18, main, lit and 'accent' or 'muted')
    label(parent, 'strip_sub', CX + 14, 80, CW - 28, 16, sub, 'hint')
end

local function draw_home(parent, act_list)
    section(parent, 'gold', PAGE_Y, '国库资金')
    local ok_f, f = pcall(faction)
    local gold = (ok_f and f.treasury) and (group_digits(f:treasury()) .. ' 金') or '—'
    label(parent, 'gold_val', CX, PAGE_Y + 30, CW - 3 * 64 - 3 * 8, 36, gold, 'value')
    for idx, item in ipairs({{10000, '+1万'}, {50000, '+5万'}, {200000, '+20万'}}) do
        local b = button(parent, 'gold_' .. tostring(item[1]), CX + CW - (4 - idx) * 64 - (3 - idx) * 8, PAGE_Y + 30, 64, 36, item[2], 'active')
        b:SetDisabled(busy)
    end
    local income_on, income_value = income_actions.read()
    button(parent, 'income_open', CX + CW - 180, PAGE_Y, 180, 24,
        '收入增幅  ' .. (income_on and ('+' .. tostring(income_value) .. '%') or '未开启') .. '  ›',
        income_on and 'enabled' or 'active')
    local ey = PAGE_Y + 30 + 36 + 14
    local resources = {}
    for index, def in ipairs(STRATEGIC.resources) do
        local resource = resource_interface(def)
        if resource then resources[#resources + 1] = {index=index, def=def, value=resource:value()} end
    end
    if #resources > 0 then
        home_resource_page = math.max(1, math.min(home_resource_page, #resources))
        local item = resources[home_resource_page]
        local key = 'resource_' .. item.index
        card(parent, 'home_resource_card', CX, ey, CW, 64, '', 'active')
        local icon = component(parent, 'home_resource_icon', 'ui/wh3pt/icon', nil, CX + 8, ey + 16, 32, 32, '', 'active')
        icon:SetImagePath(item.def.icon, 0, false)
        icon:SetTooltipText(strategic_name(item.def) .. '：当前 ' .. item.value ..
            (item.def.kind == 'sigils' and '\n设置剩余可用数量；保留已强化的符印' or ''), true)
        label(parent, 'home_resource_name', CX + 48, ey + 2, CW - 140, 24, strategic_name(item.def), 'body')
        strategic_inputs[key] = input(parent, 'home_resource_value', CX + 48, ey + 30, 88, 26,
            strategic_draft[key] or tostring(item.value))
        label(parent, 'home_resource_current', CX + 142, ey + 30, CW - 220, 26, '当前 ' .. item.value, 'quiet')
        button(parent, 'resource_do_' .. item.index, CX + CW - 68, ey + 30, 60, 26, '设置', 'active'):SetDisabled(busy)
        if #resources > 1 then
            button(parent, 'resource_prev', CX + CW - 90, ey + 2, 24, 24, '‹', 'active')
            label(parent, 'home_resource_pages', CX + CW - 64, ey + 2, 38, 24,
                home_resource_page .. '/' .. #resources, 'center')
            button(parent, 'resource_next', CX + CW - 24, ey + 2, 24, 24, '›', 'active')
        end
        ey = ey + 72
    end
    section(parent, 'quick', ey, '一键操作')
    local bw = (CW - 2 * 8) / 3
    for idx, item in ipairs({{'move_all', '补满全部军队'}, {'preset_campaign', #CAMPAIGN_PRESET .. ' 项内政与军队'},
        {'preset_battle', #BATTLE_PRESET .. ' 项战斗加成'}}) do
        local id, bx, by = item[1], CX + (idx - 1) * (bw + 8), ey + 30
        button(parent, 'cmd_' .. id, bx, by, bw, 54, '', 'accent'):SetDisabled(busy)
        label(parent, 'ptitle_' .. id, bx + 4, by + 6, bw - 8, 24, command_defs[id].short, 'center')
        label(parent, 'psub_' .. id, bx + 4, by + 29, bw - 8, 20, item[2], 'accent_center')
    end
    ey = ey + 30 + 54 + 12
    section(parent, 'active', ey, '正在生效', tostring(#act_list) .. ' 项')
    local ry0 = ey + 30
    local slots = math.max(1, math.floor((CONTENT_BOTTOM - ry0 + 6) / 38))
    if #act_list == 0 then
        card(parent, 'empty_active', CX, ry0, CW, math.min(64, CONTENT_BOTTOM - ry0), '', 'active')
        label(parent, 'empty_t1', CX + 12, ry0 + 6, CW - 24, 24, '暂无生效的修改', 'center')
        label(parent, 'empty_t2', CX + 12, ry0 + 32, CW - 24, 22, '开启任意功能后会列在这里', 'quiet_center')
        return
    end
    local show = #act_list > slots and slots - 1 or #act_list
    for i = 1, show do active_row(parent, 'act', act_list[i], CX, CW, ry0 + (i - 1) * 38) end
    if #act_list > show then
        button(parent, 'sheet_open', CX, ry0 + show * 38, CW, 32, '查看全部 ' .. tostring(#act_list) .. ' 项  ›', 'ghost_page')
    end
end

local function row_height(def) return ROW_H + (def.presets and 32 or 0) end

-- Star, name and one line of detail; the controls sit to the right of them.
local function row_head(parent, def, ry, width, lit, detail)
    local starred = favourites[def.id]
    button(parent, 'fav_' .. def.id, CX + 6, ry + 14, 26, 24, starred and '★' or '☆',
        starred and (lit and 'star_lit' or 'star') or (lit and 'ghost_lit' or 'ghost_card'))
    label(parent, 'name_' .. def.id, CX + 36, ry + 4, width, 24, def.short or def.label, 'active')
    label(parent, 'info_' .. def.id, CX + 36, ry + 27, width, 20, detail, 'hint')
end

local function draw_row(parent, def, ry)
    local ok, active, actual = pcall(read, def)
    local lit = ok and active
    local row = {def = def}
    card(parent, 'card_' .. def.id, CX, ry, ROW_W, row_height(def), '', lit and 'enabled' or 'active')
    local right = CX + ROW_W - 8
    -- One switch: a changed value on a live row applies it, otherwise it flips.
    local changed = lit and not def.toggle and tonumber(draft[def.id]) ~= actual
    button(parent, 'toggle_' .. def.id, right - 56, ry + 13, 56, 26,
        not ok and '异常' or changed and '应用' or lit and '● 开' or '○ 关',
        not ok and 'inactive' or changed and 'accent' or lit and 'enabled' or 'active'):SetDisabled(not ok or busy)
    if def.toggle then
        row_head(parent, def, ry, ROW_W - 36 - 72, lit, row_detail(def, ok))
        return row
    end
    row_head(parent, def, ry, ROW_W - 36 - 198, lit, row_detail(def, ok))
    button(parent, 'minus_' .. def.id, right - 182, ry + 13, 26, 26, '−', 'active')
    if def.choices then
        local idx = tonumber(draft[def.id]) or def.default
        button(parent, 'choice_' .. def.id, right - 154, ry + 13, 64, 26, tostring(def.choices[idx] or '?') .. def.unit, 'selected')
    else
        row.input = input(parent, 'input_' .. def.id, right - 154, ry + 13, 64, 26, draft[def.id])
    end
    button(parent, 'plus_' .. def.id, right - 88, ry + 13, 26, 26, '+', 'active')
    if def.presets then
        local pw = (ROW_W - 36 - 8 - 18) / 4
        for i, v in ipairs(def.presets) do
            button(parent, 'incomequick_' .. tostring(v), CX + 36 + (i - 1) * (pw + 6), ry + 50,
                pw, 26, '+' .. tostring(v) .. '%', 'active'):SetDisabled(not ok or busy)
        end
    end
    return row
end

local function draw_command_row(parent, cmd, ry)
    local row = {def = cmd, is_cmd = true}
    card(parent, 'card_cmd_' .. cmd.id, CX, ry, ROW_W, ROW_H, '', 'active')
    local right = CX + ROW_W - 8
    local text, disabled = cmd.btn, busy
    if cmd.id == 'free_skills' then
        text = target_actions.enabled() and '● 开' or '○ 关'
    elseif cmd.id == 'mortarchs' then
        text = mortarch_actions.enabled() and '● 开' or '○ 关'
        disabled = busy or not mortarch_actions.supported()
    elseif cmd.id == 'mortarch_buildings' then
        disabled = busy or not mortarch_actions.buildings_supported()
    end
    button(parent, 'cmd_' .. cmd.id, right - 56, ry + 13, 56, 26, text, 'accent'):SetDisabled(disabled)
    if cmd.toggle then
        row_head(parent, cmd, ry, ROW_W - 36 - 72, false, cmd.hint)
        return row
    end
    row_head(parent, cmd, ry, ROW_W - 36 - 198, false, cmd.hint)
    button(parent, 'cminus_' .. cmd.id, right - 182, ry + 13, 26, 26, '−', 'active')
    row.input = input(parent, 'cinput_' .. cmd.id, right - 154, ry + 13, 64, 26, cmd_draft[cmd.id])
    button(parent, 'cplus_' .. cmd.id, right - 88, ry + 13, 26, 26, '+', 'active')
    return row
end

local function draw_empty_state(parent, title, subtitle, action_id, action_label)
    local ey = LIST_Y + 16
    card(parent, 'empty_card', CX, ey, CW, 168, '', 'active')
    label(parent, 'empty_t1', CX + 16, ey + 30, CW - 32, 30, title, 'title_center')
    label(parent, 'empty_t2', CX + 16, ey + 66, CW - 32, 22, subtitle, 'quiet_center')
    if action_id then
        button(parent, action_id, CX + (CW - 148) / 2, ey + 108, 148, 34, action_label, 'accent')
    end
end

-- Native scroll list. ui/wh3pt/list is the engine's Listview, which clips its
-- content and handles the wheel and the slider; the panel only fills it. It
-- survives renders while its key is unchanged, which keeps the scroll position,
-- and a row is rebuilt only when its signature changes, so a click on one row
-- does not recreate the whole of a long list. Its slider has a constant step:
-- by default a wheel notch moves one child, and all rows share a single child.
local function close_list()
    if list_ui then list_ui.view:Destroy(); list_ui = nil end
end

local function child_of(parent, name)
    local found = parent:Find(name, false)
    return found and UIComponent(found) or nil
end

local function fit_part(u, w, h)
    u:SetCanResizeWidth(true); u:SetCanResizeHeight(true); u:Resize(w, h)
end

local function open_list(key, content_h)
    if list_ui and list_ui.key == key then return end
    close_list()
    local x0, y0 = px(CX), px(LIST_Y)
    local w, h = px(CX + CW) - x0, px(CONTENT_BOTTOM) - y0
    local row_w = px(CX + ROW_W) - x0
    local view = make(panel, 'list', 'ui/wh3pt/list', nil, ox + x0, oy + y0, w, h)
    local clip = assert(child_of(view, 'list_clip'), '缺少列表裁剪区')
    local box = assert(child_of(clip, 'list_box'), '缺少列表容器')
    fit_part(clip, row_w, h)
    fit_part(box, row_w, px(content_h))
    local slider = child_of(view, 'vslider')
    if slider then
        -- The slider's arrows hang outside its track, one arrow above and below.
        local arrow = child_of(slider, 'top')
        local _, arrow_h = (arrow or slider):Dimensions()
        local slider_w = slider:Dimensions()
        fit_part(slider, slider_w, h - 2 * (arrow and arrow_h or px(24)))
        pcall(function() slider:SetProperty('step_size', tostring(px(ROW_H + ROW_GAP))) end)
    end
    local content = make(box, 'list_content', 'ui/wh3pt/label', nil, ox + x0, oy + y0, row_w, px(content_h), '', 'active')
    if box.Layout then pcall(function() box:Layout() end) end
    view:PropagatePriority(200)
    list_ui = {key = key, view = view, content = content, rows = {}}
end

-- Draws one list row at design position y unless it is unchanged.
local function list_row(id, y, h, signature, draw)
    local entry = list_ui.rows[id]
    if not entry or entry.signature ~= signature then
        if entry then entry.holder:Destroy() end
        local holder = label(list_ui.content, 'row_' .. id, CX, y, ROW_W, h, '')
        entry = {signature = signature, holder = holder, row = draw(holder)}
        list_ui.rows[id] = entry
    end
    return entry.row
end

-- Runs fn with the design origin moved so that LIST_Y maps to the top of the
-- scrolled content; rows then use the same coordinates as everything else.
local function in_list(fn)
    local cx, cy = list_ui.content:Position()
    local saved_x, saved_y = ox, oy
    ox, oy = cx - px(CX), cy - px(LIST_Y)
    local ok, err = pcall(fn)
    ox, oy = saved_x, saved_y
    if not ok then error(err, 0) end
end

local function draw_catalog()
    local entries, ids, total = {}, {}, 0
    for _, page in ipairs(PAGES) do
        for index, block in ipairs((group == '全部' or group == page.name) and page.sections or {}) do
            local title = {title=(group == '全部' and (page.name .. ' · ') or '') .. block[1], id=page.name .. index}
            for i = 2, #block do
                local def = definitions[block[i]] or command_defs[block[i]]
                local mortarch = def.id == 'mortarchs' or def.id == 'mortarch_buildings'
                if (not mortarch or (mortarch_actions and mortarch_actions.supported()))
                    and (group ~= '全部' or not only_favourites or favourites[def.id])
                    and (query == '' or literal_find(def.short or def.label, query, 1, true) or literal_find(def.id, query, 1, true)) then
                    if title then
                        entries[#entries + 1] = title
                        ids[#ids + 1] = title.id
                        total = total + SECTION_H
                        title = nil
                    end
                    entries[#entries + 1] = def
                    ids[#ids + 1] = def.id
                    total = total + row_height(def) + ROW_GAP
                end
            end
        end
    end
    if #entries == 0 then
        close_list()
        if only_favourites and query == '' then
            draw_empty_state(rows_parent, '收藏夹还是空的', '在列表中点 ☆ 即可收藏常用功能', 'go_all', '浏览全部功能')
        else
            draw_empty_state(rows_parent, '未找到 “' .. (query ~= '' and query or '条件') .. '”',
                '换个关键词，或清除搜索', 'empty_clear', '清除搜索')
        end
        return
    end
    open_list(table.concat(ids, ','), total - ROW_GAP)
    in_list(function()
        local y = LIST_Y
        for _, entry in ipairs(entries) do
            if entry.title then
                local top = y
                list_row('section_' .. entry.id, y, SECTION_H, entry.title, function(holder)
                    label(holder, 'sec_' .. entry.id, CX, top + 4, ROW_W, 20, entry.title, 'section')
                    card(holder, 'div_' .. entry.id, CX, top + 25, ROW_W, 1, '', 'divider')
                end)
                y = y + SECTION_H
            else
                local top, def = y, entry
                local signature, draw
                if command_defs[def.id] then
                    signature = table.concat({tostring(cmd_draft[def.id]), tostring(favourites[def.id]), tostring(busy),
                        tostring(def.id == 'free_skills' and target_actions.enabled()),
                        tostring(def.id == 'mortarchs' and mortarch_actions.enabled())}, '|')
                    draw = draw_command_row
                else
                    local ok, active, actual = pcall(read, def)
                    signature = table.concat({tostring(ok), tostring(active), tostring(actual),
                        tostring(favourites[def.id]), tostring(draft[def.id]), tostring(busy)}, '|')
                    draw = draw_row
                end
                current_rows[#current_rows + 1] = list_row(def.id, y, row_height(def), signature, function(holder)
                    return draw(holder, def, top)
                end)
                y = y + row_height(def) + ROW_GAP
            end
        end
    end)
end

local function draw_active_sheet(act_list)
    label(rows_parent, 'sheet_title', CX, 60, CW - 46, 32, '正在生效 · ' .. tostring(#act_list) .. ' 项', 'title')
    button(rows_parent, 'sheet_close', CX + CW - 34, 62, 34, 28, '×', 'active')
    if #act_list == 0 then
        close_list()
        label(rows_parent, 'sheet_empty', CX + 16, LIST_Y + 60, CW - 32, 24, '当前没有生效的修改', 'quiet_center')
        return
    end
    local ids = {}
    for i, it in ipairs(act_list) do ids[i] = it.def.id end
    open_list('sheet:' .. table.concat(ids, ','), #act_list * 38 - 6)
    in_list(function()
        for i, it in ipairs(act_list) do
            local y = LIST_Y + (i - 1) * 38
            list_row('sheet_' .. it.def.id, y, 32, it.text .. '|' .. tostring(busy), function(holder)
                active_row(holder, 'sheet', it, CX, ROW_W, y)
            end)
        end
    end)
end

-- Keep these dense catalogues in bounded pages, including on small displays.
local function draw_strategic(parent)
    draw_search(parent)
    local matches, f, categories, category_counts, total = {}, faction(), {}, {}, 0
    for index, def in ipairs(STRATEGIC.units) do
        local name, own = strategic_name(def), regiment_belongs_to_faction(def, f)
        if not own_regiments or own then
            local category = def.category or 'other'
            categories[category] = {id=category, label=def.category_label or '其他', order=def.category_order or 999}
            if query == '' or literal_find(name, query, 1, true) or literal_find(def.id, query, 1, true) then
                total = total + 1
                category_counts[category] = (category_counts[category] or 0) + 1
                if strategic_category == 'all' or strategic_category == category then
                    matches[#matches + 1] = {index=index, def=def, name=name, own=own}
                end
            end
        end
    end
    table.sort(matches, function(a, b)
        if strategic_sort ~= 'category' then
            local av, bv = a.def[strategic_sort] or 0, b.def[strategic_sort] or 0
            if av ~= bv then return av > bv end
        end
        local ao, bo = a.def.category_order or 999, b.def.category_order or 999
        if ao ~= bo then return ao < bo end
        local ac, bc = a.def.category or 'other', b.def.category or 'other'
        if ac ~= bc then return ac < bc end
        return a.def.id < b.def.id
    end)
    local pages = math.max(1, math.ceil(#matches / 5))
    strategic_page = math.max(1, math.min(strategic_page, pages))
    button(parent, 'native_toggle', CX, LIST_Y, CW, 28,
        native_enabled and '● 原生精英无限招募：已开启' or '○ 开启原生精英无限招募', native_enabled and 'enabled' or 'active')
    button(parent, 'strategy_filter', CX, LIST_Y + 32, 132, 28, own_regiments and '本阵营精英 ▼' or '全部阵营 ▼', 'active')
    local category_name = strategic_category == 'all' and '全部类别' or (categories[strategic_category] and categories[strategic_category].label or '其他')
    button(parent, 'strategy_categories', CX + 140, LIST_Y + 32, CW - 140, 28,
        category_name .. (category_menu and ' ▲' or ' ▼'), 'active')
    local sort_labels = {recruitment_cost='招募费用 ↓', upkeep_cost='维护费 ↓', category='原生类别'}
    button(parent, 'strategy_sort', CX, LIST_Y + 64, CW, 24,
        '排序：' .. sort_labels[strategic_sort] .. '　点击切换', 'active')
    for slot = 1, 5 do
        local item = matches[(strategic_page - 1) * 5 + slot]
        if item then
            local y = LIST_Y + 92 + (slot - 1) * 64
            card(parent, 'strategy_card_' .. slot, CX, y, CW, 60, '', 'active')
            local icon = component(parent, 'strategy_icon_' .. slot, 'ui/wh3pt/icon', nil,
                CX + 6, y + 4, 28, 56, '', 'active')
            if item.def.icon and item.def.icon ~= '' then icon:SetImagePath(item.def.icon, 0, false) end
            local category = item.def.category_label or '其他'
            local tooltip = item.name .. '\n基础招募费 ' .. tostring(item.def.recruitment_cost or 0) .. ' · 基础维护费 ' .. tostring(item.def.upkeep_cost or 0) .. '\n费用不含领主、科技与阵营修正，并非战斗力评分\n' .. category .. ' · ' .. (item.def.unit_type or '') .. '\n' .. (item.def.details or '')
            icon:SetTooltipText(tooltip, true)
            local name_ui = label(parent, 'strategy_name_' .. slot, CX + 42, y + 3, CW - 48, 25, item.name, 'body')
            name_ui:SetTooltipText(tooltip, true)
            label(parent, 'strategy_info_' .. slot, CX + 42, y + 31, CW - 162, 26,
                '招募 ' .. tostring(item.def.recruitment_cost or 0) .. ' · 维护 ' .. tostring(item.def.upkeep_cost or 0), 'quiet')
            button(parent, 'strategy_do_' .. item.index, CX + CW - 112, y + 31, 104, 26,
                item.own and (native_enabled and '关闭解锁' or '开启解锁')
                or (native_selected[item.def.id] and '移出招募池' or '加入招募池'), 'active')
        end
    end
    if #matches == 0 then label(parent, 'strategy_empty', CX, LIST_Y + 100, CW, 40, '无匹配项目；可清除搜索或切换类别/阵营', 'quiet') end
    button(parent, 'strategy_prev', CX, CONTENT_BOTTOM - 32, 70, 28, '上一页', 'active')
    label(parent, 'strategy_pages', CX + 78, CONTENT_BOTTOM - 32, CW - 156, 28,
        strategic_page .. '/' .. pages .. ' · ' .. #matches .. ' 项', 'center')
    button(parent, 'strategy_next', CX + CW - 70, CONTENT_BOTTOM - 32, 70, 28, '下一页', 'active')
    if category_menu then
        local options = {}
        for _, category in pairs(categories) do options[#options + 1] = category end
        table.sort(options, function(a,b) return a.order == b.order and a.id < b.id or a.order < b.order end)
        table.insert(options, 1, {id='all', label='全部类别'})
        local my, mh = LIST_Y + 64, math.ceil(#options / 2) * 34 + 12
        overlay_parent = label(panel, 'category_overlay', CX, my, CW, mh, '')
        card(overlay_parent, 'category_background', CX, my, CW, mh, '', 'raise')
        local bw = (CW - 18) / 2
        for i, option in ipairs(options) do
            local count = option.id == 'all' and total or (category_counts[option.id] or 0)
            button(overlay_parent, 'strategycat_' .. option.id,
                CX + 6 + ((i - 1) % 2) * (bw + 6), my + 6 + math.floor((i - 1) / 2) * 34,
                bw, 30, option.label .. ' · ' .. count, strategic_category == option.id and 'selected' or 'active')
        end
        overlay_parent:PropagatePriority(230)
    end
end

render = function()
    if not panel then return end
    faction()
    capture()
    ox, oy = panel:Position()   -- the player may have dragged the panel
    if rows_parent then rows_parent:Destroy(); rows_parent = nil end
    if overlay_parent then overlay_parent:Destroy(); overlay_parent = nil end
    current_rows, search = {}, nil
    strategic_inputs = {}
    local act_list = active_items()
    local total = #act_list

    if rail_count_ui then refit(rail_count_ui, tostring(total) .. ' 项', total > 0 and 'enabled' or 'active') end
    if launcher then
        refit(launcher, visible and '◆ 修改器 [F9]' or ('◆ 修改器 · ' .. tostring(total)),
            visible and 'selected' or (total > 0 and 'enabled' or 'active'))
    end
    for _, item in ipairs(PAGES) do
        local tab_btn = controls[PREFIX .. 'tab_' .. item.name]
        if tab_btn then refit(tab_btn, item.name, (not collapsed and group == item.name) and 'selected' or 'ghost') end
    end
    if collapsed then return end
    if title_ui then refit(title_ui, active_sheet and '正在生效' or PAGES[group].title) end

    -- rows_parent holds what stays put; the scroll list is its sibling, below it.
    local list_page = active_sheet or (group ~= '总览' and group ~= '精英')
    rows_parent = label(panel, 'rows', CX, 56, CW, FOOTER_Y - 4 - 56, '')
    if not list_page then close_list() end
    if active_sheet then
        draw_active_sheet(act_list)
    elseif group == '总览' then
        draw_home(rows_parent, act_list)
    elseif group == '精英' then
        draw_strategic(rows_parent)
    else
        draw_strip(rows_parent)
        draw_catalog()
    end
end

-- The launcher sits in the free stretch of the top bar, left of the native tab
-- strip; the fallback offset keeps it there when the strip cannot be found.
local function launcher_x(rw, lw)
    local ok, left = pcall(function()
        local tabs = type(find_uicomponent) == 'function'
            and find_uicomponent(core:get_ui_root(), 'hud_campaign', 'bar_small_top')
        if tabs then return (tabs:Position()) end
    end)
    if ok and type(left) == 'number' and left > rw * 0.5 then
        return math.max(0, left - px(LAUNCHER_CLEARANCE) - lw)
    end
    return math.max(0, rw - EDGE_RIGHT - lw - math.floor(rw * 0.16))
end

-- Dragging is the engine's: SetMoveable, the mechanism of the undocked help panel.
-- homes holds where each draggable was last placed, to tell a drag from a click.
local homes = {}

local function store_point(u, name)
    local x, y = u:Position()
    cm:set_saved_value(PREFIX .. name .. '_x', x)
    cm:set_saved_value(PREFIX .. name .. '_y', y)
end

local function left_home(u, name)
    local x, y = u:Position()
    local home = homes[name]
    if not home or math.abs(x - home[1]) + math.abs(y - home[2]) <= DRAG_SLOP then return false end
    homes[name] = {x, y}
    store_point(u, name)
    return true
end

local function place(u, name, default_x, default_y, max_x, max_y)
    local x, y = cm:get_saved_value(PREFIX .. name .. '_x'), cm:get_saved_value(PREFIX .. name .. '_y')
    if type(x) ~= 'number' or type(y) ~= 'number' then x, y = default_x, default_y end
    x, y = clamp(x, 0, max_x), clamp(y, 0, max_y)
    u:MoveTo(x, y)
    homes[name] = {x, y}
    if u.SetMoveable then pcall(function() u:SetMoveable(true) end) end
    return x, y
end

create_ui = function()
    local root = core:get_ui_root()
    if panel then left_home(panel, 'panel'); capture(); panel:Destroy() end
    if launcher then left_home(launcher, 'launcher'); launcher:Destroy() end
    panel, launcher, rows_parent, overlay_parent, title_ui, rail_count_ui = nil, nil, nil, nil, nil, nil
    search, status_ui, status_dot_ui, list_ui = nil, nil, nil, nil
    current_rows, controls, strategic_inputs = {}, {}, {}
    local rw, rh = compute_scale(root)
    local panel_h = collapsed and RAIL_H or DESIGN_H
    local pw, ph = px(collapsed and RAIL_W or DESIGN_W), px(panel_h)
    local lw, lh = px(LAUNCHER_W), px(LAUNCHER_H)

    launcher = make(root, 'open', 'ui/wh3pt/button', 'button', 0, 0, lw, lh,
        '◆ 修改器 [F9]', visible and 'selected' or 'active')
    place(launcher, 'launcher', launcher_x(rw, lw), EDGE_TOP, rw - lw, rh - lh)
    controls[PREFIX .. 'open'] = launcher
    panel = make(root, 'panel', 'ui/wh3pt/card', 'card', 0, 0, pw, ph, '', 'panel_bg')
    ox, oy = place(panel, 'panel', math.max(0, rw - EDGE_RIGHT - pw), EDGE_TOP + px(LAUNCHER_H + LAUNCHER_GAP),
        rw - pw, rh - ph)

    -- Navigation rail (also the whole panel when collapsed).
    local inner_h = panel_h - 2 * FRAME_INSET
    card(panel, 'rail_bg', FRAME_INSET, FRAME_INSET, RAIL_W - FRAME_INSET * (collapsed and 2 or 1), inner_h, '', 'rail_bg')
    card(panel, 'logo_box', (RAIL_W - 34) / 2, 10, 34, 34, '◆', 'logo')
    for i, item in ipairs(PAGES) do
        button(panel, 'tab_' .. item.name, 8, 56 + (i - 1) * 36, RAIL_W - 16, 32, item.name,
            (not collapsed and group == item.name) and 'selected' or 'ghost')
    end
    if collapsed then
        rail_count_ui = button(panel, 'rail_count', 8, RAIL_H - 86, RAIL_W - 16, 30, '0 项', 'active')
        button(panel, 'collapse_rail', 8, RAIL_H - 48, RAIL_W - 16, 36, '‹', 'active')
    else
        -- Active count and clear-all live on the rail so the status line gets the
        -- whole footer: failure messages are its longest text and must stay readable.
        button(panel, 'clear', 8, FOOTER_Y - 30, RAIL_W - 16, 30, '全关', 'danger')
        rail_count_ui = button(panel, 'rail_count', 8, FOOTER_Y + 8, RAIL_W - 16, 30, '0 项', 'active')
        card(panel, 'rail_div', RAIL_W, FRAME_INSET, 1, inner_h, '', 'divider')
        -- Header: page title + scale / collapse / close.
        title_ui = label(panel, 'title', CX, 12, CW - 156, 30, PAGES[group].title, 'title')
        button(panel, 'ui_scale', CX + CW - 146, 13, 76, 28, scale_label(), 'ghost_page')
        button(panel, 'collapse', CX + CW - 64, 13, 30, 28, '—', 'active')
        button(panel, 'close', CX + CW - 30, 13, 30, 28, '×', 'active')
        card(panel, 'header_div', RAIL_W + 1, 51, DESIGN_W - RAIL_W - 1 - FRAME_INSET, 1, '', 'divider')
        -- Footer: status line.
        card(panel, 'footer_div', RAIL_W + 1, FOOTER_Y, DESIGN_W - RAIL_W - 1 - FRAME_INSET, 1, '', 'divider')
        status_dot_ui = card(panel, 'status_dot', CX, FOOTER_Y + 19, 8, 8, '', status_error and 'danger_dot' or 'ok_dot')
        status_ui = label(panel, 'status', CX + 16, FOOTER_Y + 8, CW - 16, 30, status, 'muted')
    end

    render()
    panel:PropagatePriority(200)
    panel:SetVisible(visible)
end

local function click(id)
    local previous = faction_name
    faction()
    if previous ~= faction_name then
        render()
        message('已切换到当前玩家阵营，请重新选择操作')
        return
    end
    if id == 'open' then
        visible = not visible
        active_sheet = false
        if visible then create_ui() else
            if panel then panel:SetVisible(false) end
            if launcher then refit(launcher, '◆ 修改器 [F9]', 'active') end
        end
        return
    elseif id == 'close' then
        capture()
        visible = false
        active_sheet = false
        if panel then panel:SetVisible(false) end
        if launcher then refit(launcher, '◆ 修改器 [F9]', 'active') end
        return
    elseif id == 'collapse' or id == 'collapse_rail' then
        capture()
        collapsed = not collapsed
        active_sheet = false
        create_ui()
        return
    elseif id == 'ui_scale' then
        capture()
        local nxt = 1
        for i, v in ipairs(UI_SCALE_STEPS) do
            if math.abs(v - ui_scale_pref) < 0.001 then nxt = i % #UI_SCALE_STEPS + 1 end
        end
        ui_scale_pref = UI_SCALE_STEPS[nxt]
        cm:set_saved_value(PREFIX .. 'ui_scale', ui_scale_pref)
        create_ui()
        message(ui_scale_pref > 0 and ('界面缩放：' .. tostring(math.floor(ui_scale_pref * 100 + 0.5)) .. '%')
            or ('界面缩放：自动（检测 ' .. tostring(math.floor((ui_info.auto or 1) * 100 + 0.5)) .. '%）'))
        return
    elseif id == 'rail_count' or id == 'sheet_open' then
        capture()
        if collapsed then
            collapsed = false
            active_sheet = true
            create_ui()
            return
        end
        active_sheet = id == 'sheet_open' or not active_sheet
    elseif id == 'sheet_close' then
        active_sheet = false
    elseif id == 'favourites_only' then
        capture()
        only_favourites = not only_favourites
    elseif id == 'go_all' then
        capture()
        group, query, search_text, active_sheet, only_favourites = '全部', '', '', false, false
        if search then search:SetStateText('') end
        if collapsed then collapsed = false; create_ui(); return end
    elseif id == 'search_go' then
        strategic_page, category_menu = 1, false
        if search then search_text = search:GetStateText(); query = search_text end
        active_sheet = false
    elseif id == 'search_clear' or id == 'empty_clear' then
        strategic_page, category_menu = 1, false
        query, search_text, only_favourites = '', '', false
        if search then search:SetStateText('') end
    elseif id == 'clear' then clear_all(); return
    elseif substring(id, 1, 5) == 'gold_' then
        local amount = substring(id, 6)
        run_command_action('treasury', amount)
        return
    elseif substring(id, 1, 4) == 'off_' then
        local key = substring(id, 5)
        if definitions[key] then apply(key, false, draft[key]); return end
    elseif id == 'native_toggle' then
        set_native_recruitment(not native_enabled)
    elseif substring(id, 1, 10) == 'elite_tab_' then
        elite_tabs.select(substring(id, 11))
        arrange_native_cards()
        return
    elseif id == 'resource_prev' or id == 'resource_next' then
        capture(); home_resource_page = math.max(1, home_resource_page + (id == 'resource_next' and 1 or -1))
    elseif substring(id, 1, 12) == 'resource_do_' then
        capture()
        local index = tonumber(substring(id, 13))
        set_resource(index, strategic_draft['resource_' .. index]); return
    elseif id == 'strategy_prev' or id == 'strategy_next' then
        capture()
        strategic_page = math.max(1, strategic_page + (id == 'strategy_next' and 1 or -1))
    elseif id == 'strategy_filter' then
        capture(); own_regiments = not own_regiments; strategic_page = 1
        strategic_category, category_menu = 'all', false
    elseif id == 'strategy_sort' then
        capture(); strategic_sort = strategic_sort == 'recruitment_cost' and 'upkeep_cost' or strategic_sort == 'upkeep_cost' and 'category' or 'recruitment_cost'
        strategic_page, category_menu = 1, false
    elseif id == 'strategy_categories' then
        capture(); category_menu = not category_menu
    elseif substring(id, 1, 12) == 'strategycat_' then
        capture(); strategic_category = substring(id, 13)
        strategic_page, category_menu = 1, false
    elseif substring(id, 1, 12) == 'strategy_do_' then
        capture()
        local index = tonumber(substring(id, 13))
        select_native_regiment(index)
        return
    elseif id == 'income_open' then
        capture(); group = '内政'; query, search_text = '', ''
        active_sheet = false
    elseif substring(id, 1, 12) == 'incomequick_' then
        capture(); apply('income', true, substring(id, 13)); return
    elseif substring(id, 1, 4) == 'tab_' then
        capture()
        group = substring(id, 5)
        strategic_page = 1
        category_menu = false
        query, search_text, only_favourites = '', '', false
        if search then search:SetStateText('') end
        active_sheet = false
        if collapsed then collapsed = false; create_ui(); return end
    elseif substring(id, 1, 4) == 'cmd_' then
        capture()
        local key = substring(id, 5)
        run_command_action(key, cmd_draft[key])
        return
    elseif substring(id, 1, 6) == 'cplus_' or substring(id, 1, 7) == 'cminus_' then
        capture()
        local is_minus = substring(id, 1, 7) == 'cminus_'
        local key = substring(id, is_minus and 8 or 7)
        local cmd = command_defs[key]
        if not cmd then return end
        local n = validate(cmd, cmd_draft[key])
        n = math.max(cmd.step, math.min(cmd.max, n + (is_minus and -cmd.step or cmd.step)))
        cmd_draft[key] = tostring(n)
        for _, row in ipairs(current_rows) do
            if row.is_cmd and row.def.id == key and row.input then row.input:SetStateText(cmd_draft[key]) end
        end
    else
        local action, key = string.match(id, '^([^_]+)_(.+)$')
        local def = definitions[key]
        if action == 'fav' and (def or command_defs[key]) then
            capture()
            favourites[key] = not favourites[key]
            cm:set_saved_value(setting(key, 'favourite'), favourites[key])
        elseif not def then
            return
        elseif action == 'plus' or action == 'minus' or action == 'choice' then
            capture()
            local n = validate(def, draft[key])
            local low = def.min or (def.choices and 1 or 0)
            n = math.max(low, math.min(def.max, n + (action == 'minus' and -def.step or def.step)))
            if action == 'choice' and n == tonumber(draft[key]) then n = 1 end
            draft[key] = tostring(n)
            for _, row in ipairs(current_rows) do if row.def.id == key and row.input then row.input:SetStateText(draft[key]) end end
        elseif action == 'toggle' then
            capture()
            local on, actual = read(def)
            if on and not def.toggle and tonumber(draft[key]) ~= actual then apply(key, true, draft[key])
            else apply(key, not on, draft[key]) end
            return
        end
    end
    render()
end

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
    selected_cqi, busy, native_pending = nil, false, false
    native_revision = native_revision + 1
    current_rows, strategic_inputs, strategic_draft = {}, {}, {}
    query, search_text, search = '', '', nil
    strategic_page, home_resource_page, own_regiments = 1, 1, true
    strategic_category, category_menu = 'all', false
    active_sheet, only_favourites = false, false
    close_list()
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
    for index, def in ipairs(STRATEGIC.resources) do
        strategic_draft['resource_' .. index] = cm:get_saved_value(setting('resource_' .. def.id, 'target'))
    end
    native_enabled = cm:get_saved_value(setting('native_ror', 'enabled')) == true
    native_selected = {}
    for _, def in ipairs(STRATEGIC.units) do
        native_selected[def.id] = cm:get_saved_value(setting('native_ror', def.id)) == true
    end
    status, status_error = '已匹配当前玩家阵营', false
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
        character=function() return selected_cqi and cm:get_character_by_cqi(selected_cqi) or nil end,
        callback=player_callback, guard=guarded, render=function() if visible then guarded(render) end end})
    mortarch_actions = create_mortarch_actions({faction=faction, message=message, callback=player_callback, guard=guarded})
    core:remove_listener('wh3pt_unit_card_select')
    core:remove_listener('wh3pt_free_skill_click')
    core:add_listener('wh3pt_free_skill_click', 'ComponentRClickUp', true, function(c)
        guarded(function() target_actions.skill(c.component) end)
    end, true)
    local saved_scale = cm:get_saved_value(PREFIX .. 'ui_scale')
    ui_scale_pref = 0
    for _, v in ipairs(UI_SCALE_STEPS) do
        if type(saved_scale) == 'number' and math.abs(saved_scale - v) < 0.001 then ui_scale_pref = v end
    end
    for _, event in ipairs({'UnitTrained', 'UnitCreated', 'RecruitmentItemIssuedByPlayer', 'FactionTurnStart', 'CharacterSelected', 'PanelOpenedCampaign'}) do
        local name = PREFIX .. 'native_' .. event
        core:remove_listener(name)
        core:add_listener(name, event, true, function()
            guarded(function()
                faction()
                if native_enabled then schedule_native_sync(false) end
            end)
        end, true)
    end
    core:remove_listener('wh3pt_native_recruit_click')
    core:add_listener('wh3pt_native_recruit_click', 'ComponentLClickUp', function(c)
        if not native_enabled or type(c.string) ~= 'string' or substring(c.string, 1, #PREFIX) == PREFIX then return false end
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
            if id == 'open' and left_home(launcher, 'launcher') then return end
            click(id)
        end)
    end, true)

    core:remove_listener('wh3pt_moved')
    core:add_listener('wh3pt_moved', 'ComponentMoved', function(c)
        return c.string == PREFIX .. 'panel' or c.string == PREFIX .. 'open'
    end, function(c)
        guarded(function()
            if c.string == PREFIX .. 'panel' then store_point(panel, 'panel') else store_point(launcher, 'launcher') end
        end)
    end, true)

    core:remove_listener('wh3pt_hotkey')
    core:add_listener('wh3pt_hotkey', 'ShortcutTriggered', function(c)
        return is_toggle_shortcut(c.string)
    end, function() guarded(function() click('open') end) end, true)

    core:remove_listener('wh3pt_char_select')
    core:add_listener('wh3pt_char_select', 'CharacterSelected', function(c)
        return c and c.character ~= nil
    end, function(c)
        guarded(function()
            faction()
            local ch = c:character()
            if ch and not ch:is_null_interface() and ch.faction and not ch:faction():is_null_interface()
                and ch:faction():name() == faction_name then
                selected_cqi = ch:cqi()
                if visible and render then render() end
            else
                selected_cqi = nil
                if visible and render then render() end
            end
        end)
    end, true)

    -- lib_campaign_ui clears its selection on the same two events. CharacterDeselected
    -- fires only when no other character takes over, so a switch keeps the new one.
    for _, event in ipairs({'CharacterDeselected', 'SettlementSelected'}) do
        local name = PREFIX .. 'clear_selection_' .. event
        core:remove_listener(name)
        core:add_listener(name, event, true, function()
            guarded(function()
                selected_cqi = nil
                if visible and render then render() end
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
    targets=function() return target_actions end,
    start=start, read=read, apply=apply, clear=clear_all, validate=validate,
    strategic=STRATEGIC, set_resource=set_resource, native_toggle=set_native_recruitment,
    native_select=select_native_regiment, native_sync=schedule_native_sync, native_filter=arrange_native_cards,
    elite_tabs=function() return elite_tabs end,
    regiment_belongs=regiment_belongs_to_faction, resource_interface=resource_interface,
    click=click, command=run_command_action, catalog=CATALOG, commands=COMMANDS, pages=PAGES,
    layout=function() return {scale=S, text_scale=TS, auto=ui_info.auto, clamped=ui_info.clamped,
        probe_w=ui_info.probe_w, res_w=ui_info.res_w, root_w=ui_info.root_w} end,
}
