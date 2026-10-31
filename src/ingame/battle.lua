-- Separate battle runtime: campaign Lua and cm do not exist during combat.
local function new_battle_trainer(manager, spell_keys)
    spell_keys = spell_keys or SPELL_KEYS or {}
    local stopped, started, mode, final_phase = false, false, 'inherit', false
    local magic_on, cooldown_on = false, false
    local campaign_revive_rate
    local magic_ui, cooldown_ui
    local spell_controllers = {}
    local tracked, panel, status_ui, mode_ui = {}, nil, nil, nil
    local last_status = '复活默认跟随战役开关；仅作用于玩家控制部队'
    local rate = {0.01, 0.05, 0.125, 0.25}
    local api = {}
    local function allowed()
        return not stopped and not manager:is_multiplayer() and not manager:is_replay()
    end
    local function log(text)
        local logger = type(ModLog) == 'function' and ModLog or out
        logger('[WH3PT battle] ' .. text)
    end
    local function report(text)
        last_status = text
        if status_ui then status_ui:SetStateText(text); status_ui:SetTooltipText(text, true) end
        log(text)
    end
    local function guard(fn)
        local ok, err = pcall(fn)
        if not ok then report('操作失败：' .. tostring(err)) end
        return ok
    end
    local function units()
        local result = {}
        local armies = manager:get_player_alliance():armies()
        for a = 1, armies:count() do
            local list = armies:item(a):units()
            for i = 1, list:count() do
                local u = list:item(i)
                if u:is_player_controlled() then result[#result + 1] = u end
            end
        end
        return result
    end
    local function update_magic_buttons()
        if magic_ui then magic_ui:SetStateText('无限魔法之风：' .. (magic_on and '开' or '关')); magic_ui:SetState(magic_on and 'enabled' or 'active') end
        if cooldown_ui then cooldown_ui:SetStateText('法术无需冷却：' .. (cooldown_on and '开' or '关')); cooldown_ui:SetState(cooldown_on and 'enabled' or 'active') end
    end
    local function refill_magic()
        local affected = 0
        local armies = manager:get_player_alliance():armies()
        for i = 1, armies:count() do
            local army = armies:item(i)
            if army:is_player_controlled() then
                local current, reserve = army:winds_of_magic_current(), army:winds_of_magic_reserve()
                assert(type(current) == 'number' and current >= 0 and current < 1000000
                    and type(reserve) == 'number' and reserve >= 0 and reserve < 1000000, '魔法池读取异常')
                -- Direct addition cannot drain reserves. Never reduce higher
                -- pools provided by the game, other effects or storm battles.
                if reserve < 100 then army:modify_winds_of_magic_reserve(100 - reserve) end
                if current < 30 then army:modify_winds_of_magic_current(30 - current, true) end
                assert(army:winds_of_magic_current() >= math.max(30, current) - 0.01
                    and army:winds_of_magic_reserve() >= math.max(100, reserve) - 0.01,
                    '魔法池补充回读未达到目标')
                affected = affected + 1
            end
        end
        return affected
    end
    local function reset_spell_cooldowns()
        local requested = 0
        for _, u in ipairs(units()) do
            if u:is_deployed() and u:number_of_men_alive() > 0 then
                local spells = {}
                for _, key in pairs(u:owned_non_passive_special_abilities()) do
                    if spell_keys[key] then spells[#spells + 1] = key end
                end
                if #spells > 0 then
                    local army = u:army()
                    local id = army:unique_id()
                    local uc = spell_controllers[id]
                    if not uc then uc = army:create_unit_controller(); spell_controllers[id] = uc end
                    -- An issued order can implicitly take script control. Always
                    -- release, even if a reset throws; never halt or move units.
                    local ok, err = pcall(function()
                        uc:clear_all()
                        uc:add_units(u)
                        for _, key in ipairs(spells) do uc:reset_ability_cooldown(key); requested = requested + 1 end
                    end)
                    local released, release_err = pcall(function() uc:release_control(); uc:clear_all() end)
                    if not released then error(release_err) end
                    if not ok then error(err) end
                end
            end
        end
        return requested
    end
    function api.magic_tick()
        if not allowed() then return end
        local armies, spells = 0, 0
        if magic_on then
            local ok, result = pcall(refill_magic)
            if ok then armies = result
            else magic_on = false; report('无限魔法之风已暂停：' .. tostring(result)) end
        end
        if cooldown_on then
            local ok, result = pcall(reset_spell_cooldowns)
            if ok then spells = result
            else cooldown_on = false; report('法术无需冷却已暂停：' .. tostring(result)) end
        end
        update_magic_buttons()
        return armies, spells
    end
    local function track(u)
        local key = u:unique_ui_id()
        if not tracked[key] then
            local entry = {unit=u, rate=0, abilities={}}
            for _, ability in pairs(u:owned_passive_special_abilities()) do
                for index, amount in ipairs(rate) do
                    if ability == 'wh3pt_revive_' .. index then
                        entry.rate = math.max(entry.rate, amount)
                        entry.abilities[#entry.abilities + 1] = ability
                    end
                end
            end
            -- Campaign preference remains authoritative even if native passive
            -- assignment has not reached the battle unit when it is created.
            if campaign_revive_rate ~= nil then entry.rate = campaign_revive_rate end
            tracked[key] = entry
        end
        return tracked[key]
    end
    local function active(entry)
        return mode == 'on' or (mode == 'inherit' and entry.rate > 0)
    end
    local function suppress_native(entry, value)
        -- Own only the trainer resurrection passive. Other healing stays intact.
        for _, ability in ipairs(entry.abilities) do
            entry.unit:disable_special_ability(ability, value)
        end
    end
    function api.tick(full)
        if not allowed() then return end
        local restored, missing, failures, affected = 0, 0, 0, 0
        for _, u in ipairs(units()) do
            local ok, err = pcall(function()
                local entry = track(u)
                suppress_native(entry, true)
                if not active(entry) or not u:is_deployed() then return end
                affected = affected + 1
                local before = u:number_of_men_alive()
                local target = full and 1 or math.min(1, u:unary_hitpoints() + (entry.rate > 0 and entry.rate or 0.05))
                u:heal_hitpoints_unary(target, true)
                local after = u:number_of_men_alive()
                if after <= before and after < u:initial_number_of_men() and target >= 1 then
                    -- Keep native recovery available if this unit cannot be
                    -- resurrected by the direct engine method (e.g. handlers).
                    suppress_native(entry, false)
                end
                restored = restored + math.max(0, after - before)
                missing = missing + math.max(0, u:initial_number_of_men() - after)
            end)
            if not ok then
                failures = failures + 1
                pcall(function() local entry = track(u); if active(entry) then suppress_native(entry, false) end end)
                log(tostring(err))
            end
        end
        if affected > 0 or failures > 0 then
            last_status = '复活 +' .. restored .. '人 · 缺 ' .. missing .. '人 · 异常 ' .. failures .. '队'
            if status_ui then status_ui:SetStateText(last_status) end
            if full then log(last_status) end
        end
        return restored, missing, failures
    end
    function api.action(action)
        assert(allowed(), '战斗已结束，或属于多人/录像模式')
        if action == 'magic' or action == 'cooldown' then
            if action == 'magic' then magic_on = not magic_on else cooldown_on = not cooldown_on end
            local enabling = action == 'magic' and magic_on or action == 'cooldown' and cooldown_on
            if not enabling then
                update_magic_buttons()
                report((action == 'magic' and '无限魔法之风' or '法术无需冷却') .. '：已关闭')
                return
            end
            local armies, spells = api.magic_tick()
            if action == 'magic' and magic_on then report('无限魔法之风：持续补充 ' .. armies .. ' 支玩家军队')
            elseif action == 'cooldown' and cooldown_on then report('法术无需冷却：持续请求清除 ' .. spells .. ' 项法术冷却')
            end
            return
        end
        if action == 'mode' then
            mode = mode == 'inherit' and 'on' or mode == 'on' and 'off' or 'inherit'
            if mode_ui then mode_ui:SetStateText('持续复活：' .. ({inherit='跟随战役', on='开', off='关'})[mode]) end
            report('持续复活：' .. ({inherit='跟随战役设置', on='当前玩家部队开启', off='关闭'})[mode])
            return
        end
        assert(action == 'revive' or action == 'heal' or action == 'ammo', '未知战斗操作')
        local snapshots, failures = {}, 0
        for _, u in ipairs(units()) do
            local ok, err = pcall(function()
                if not u:is_deployed() then return end
                local snap = {u=u, men=u:number_of_men_alive(), hp=u:unary_hitpoints()}
                if action == 'ammo' then
                    if u:starting_ammo() <= 0 then return end
                    snap.ammo = u:ammo_left()
                    u:set_current_ammo_unary(1)
                else
                    u:heal_hitpoints_unary(1, action == 'revive')
                end
                snapshots[#snapshots + 1] = snap
            end)
            if not ok then failures = failures + 1; log(tostring(err)) end
        end
        report('已请求操作，正在回读实际人数/状态')
        manager:callback(function()
            if not allowed() then return end
            guard(function()
                local restored, missing, changed = 0, 0, 0
                for _, snap in ipairs(snapshots) do
                    local u = snap.u
                    local after = u:number_of_men_alive()
                    restored = restored + math.max(0, after - snap.men)
                    missing = missing + math.max(0, u:initial_number_of_men() - after)
                    if action == 'ammo' then
                        if u:ammo_left() > snap.ammo then changed = changed + 1 end
                    elseif u:unary_hitpoints() > snap.hp or after > snap.men then changed = changed + 1 end
                end
                report('变化' .. changed .. '队 · 复活+' .. restored .. ' · 缺' .. missing .. ' · 异常' .. failures)
            end)
        end, 100, 'wh3pt_battle_readback')
    end
    function api.ui()
        if panel or not allowed() then return end
        local root = core:get_ui_root()
        local w, h = root:Dimensions()
        local s = math.max(1, math.min(2, w / 1280, h / 720))
        local x, y = math.max(8, w - 394 * s), 75 * s
        local function make(parent, id, template, dx, dy, width, height, text, state)
            local c = UIComponent(parent:CreateComponent('wh3pt_battle_' .. id, 'ui/wh3pt/' .. template))
            c:SetDockingPoint(0)
            if c.SetDockOffset then c:SetDockOffset(0, 0) end
            c:SetCanResizeWidth(true); c:SetCanResizeHeight(true)
            c:Resize(math.floor(width * s), math.floor(height * s))
            c:MoveTo(math.floor(x + dx * s), math.floor(y + dy * s))
            c:SetState(state or 'active')
            if text then c:SetStateText(text) end
            c:PropagatePriority(210)
            return c
        end
        make(root, 'open', 'button', 172, -40, 210, 32, '◆ 战斗修改器 [F9]', 'accent')
        panel = make(root, 'panel', 'card', 0, 0, 382, 452, '', 'active')
        make(panel, 'title', 'label', 16, 10, 310, 28, '战斗修改器 · 玩家部队')
        make(panel, 'close', 'button', 334, 8, 32, 28, '×')
        mode_ui = make(panel, 'mode', 'button', 16, 52, 350, 36, '持续复活：跟随战役', 'accent')
        make(panel, 'revive', 'button', 16, 98, 350, 36, '立即复活并补满生命')
        make(panel, 'heal', 'button', 16, 144, 350, 36, '治疗存活成员')
        make(panel, 'ammo', 'button', 16, 190, 350, 36, '补满弹药')
        magic_ui = make(panel, 'magic', 'button', 16, 236, 350, 36, '')
        cooldown_ui = make(panel, 'cooldown', 'button', 16, 282, 350, 36, '')
        update_magic_buttons()
        magic_ui:SetTooltipText('继承战役设置，可在本场独立切换；每0.1秒补充玩家军队魔法池至至少30、储备至至少100。关闭后停止补充。', true)
        cooldown_ui:SetTooltipText('继承战役设置，可在本场独立切换；每0.1秒清除已掌握法术的冷却。保留施法动作、目标条件和有限使用次数。', true)
        make(panel, 'hint1', 'label', 16, 330, 350, 24, '法术仍需施法动作、有效目标及可用次数', 'hint')
        make(panel, 'hint2', 'label', 16, 354, 350, 24, '复活以开战人数为上限；仅手动战斗', 'hint')
        status_ui = make(panel, 'status', 'label', 16, 386, 350, 56, last_status, 'hint')
        status_ui:SetTooltipText(last_status, true)
        panel:SetVisible(false)
    end
    function api.start()
        if started then return false end
        local pending = core:svr_load_bool('wh3pt_magic_pending')
        core:svr_save_bool('wh3pt_magic_pending', false)
        if not allowed() then return false end
        started = true
        guard(function()
            if not pending then return end
            local owner = core:svr_load_string('wh3pt_magic_owner')
            local armies = manager:get_player_alliance():armies()
            for i = 1, armies:count() do
                local army = armies:item(i)
                if army:is_player_controlled() and army:faction_key() == owner then
                    magic_on = core:svr_load_bool('wh3pt_magic_enabled') == true
                    cooldown_on = core:svr_load_bool('wh3pt_cooldown_enabled') == true
                    local level = tonumber(core:svr_load_string('wh3pt_revive_level'))
                    if level == 0 then campaign_revive_rate = 0
                    elseif level and rate[level] then campaign_revive_rate = rate[level] end
                    report('已载入战役设置；复活：' .. (campaign_revive_rate and campaign_revive_rate > 0 and '开' or '关') .. '；本场可独立切换')
                    break
                end
            end
        end)
        core:add_listener('wh3pt_battle_click', 'ComponentLClickUp', function(c)
            return c.string == 'wh3pt_battle_open' or c.string == 'wh3pt_battle_close'
                or c.string == 'wh3pt_battle_mode' or c.string == 'wh3pt_battle_revive'
                or c.string == 'wh3pt_battle_heal' or c.string == 'wh3pt_battle_ammo'
                or c.string == 'wh3pt_battle_magic' or c.string == 'wh3pt_battle_cooldown'
        end, function(c) guard(function()
            if c.string == 'wh3pt_battle_open' or c.string == 'wh3pt_battle_close' then
                if panel then panel:SetVisible(not panel:Visible(true)) end
            else api.action(string.gsub(c.string, '^wh3pt_battle_', '')) end
        end) end, true)
        core:add_listener('wh3pt_battle_hotkey', 'ShortcutTriggered', function(c)
            return c.string == 'script_F9' or c.string == 'F9' or c.string == 'f9'
                or c.string == 'camera_bookmark_view0' or c.string == 'toggle_stream_pause'
                or c.string == 'script_F8' or c.string == 'F8'
        end, function() if panel then panel:SetVisible(not panel:Visible(true)) end end, true)
        manager:real_callback(function() guard(api.ui) end, 1000, 'wh3pt_battle_ui')
        manager:repeat_callback(function() guard(function() api.tick(final_phase) end) end, 1000, 'wh3pt_battle_revival')
        manager:repeat_callback(function() guard(api.magic_tick) end, 100, 'wh3pt_battle_magic')
        manager:register_phase_change_callback('VictoryCountdown', function()
            final_phase = true
            guard(function() api.tick(true) end)
        end)
        manager:register_phase_change_callback('Complete', function()
            stopped = true
            manager:remove_process('wh3pt_battle_revival')
            manager:remove_process('wh3pt_battle_magic')
            magic_on, cooldown_on, spell_controllers = false, false, {}
            if panel then panel:SetVisible(false) end
        end)
        log('Started: revival timer 1000ms; magic timer 100ms; F9 panel registered')
        return true
    end
    function api.status() return last_status, mode end
    function api.magic_state() return magic_on, cooldown_on end
    return api
end

-- Native battle loader loads chunks, but does NOT call core:execute_mods().
-- Start on its completion event; loading-screen dismissal is an idempotent fallback.
local runtime
function wh3pt_battle()
    if runtime then return runtime end
    if not bm or not core then return end
    local ok, err = pcall(function()
        local candidate = new_battle_trainer(bm)
        if candidate.start() then runtime = candidate end
    end)
    if not ok then
        local logger = type(ModLog) == 'function' and ModLog or out
        logger('[WH3PT battle] Startup failed: ' .. tostring(err))
    end
    return runtime
end
if core then
    for _, event in ipairs({'ScriptEventAllModsLoaded', 'LoadingScreenDismissed'}) do
        core:add_listener('wh3pt_battle_boot_' .. event, event, true, wh3pt_battle, true)
    end
end

return {new=new_battle_trainer}
