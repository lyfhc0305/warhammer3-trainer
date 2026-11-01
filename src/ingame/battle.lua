-- Separate battle runtime: campaign Lua and cm do not exist during combat.
local function new_battle_trainer(manager)
    local stopped, started, mode, final_phase = false, false, 'inherit', false
    local magic_on, cooldown_on, ability_wait = false, false, 0
    local silence_on, silenced, silence_ready, silence_blocked, silence_removed = false, {}, false, 0, 0
    local fatigue_on, fatigue_text, fatigue_tries, fatigue_cleared = false, '', {}, {}
    local campaign_revive_rate
    local magic_ui, cooldown_ui, fatigue_ui, silence_ui
    local general_ui, doctrine_ui, pick_ui, general_line, key_ui
    -- The commander's hotkey is one of the game's own shortcuts: scripts never see raw
    -- keys, only shortcuts by name, on whatever key the player has them.
    local DEFAULT_HOTKEY, HOTKEY_SETTING = 'camera_bookmark_save0', 'wh3pt_general_hotkey'
    local LAUNCHER_TEXT = '◆ 修改器 [F9]'
    local PANEL_KEYS = {script_F9=true, F9=true, f9=true, camera_bookmark_view0=true,
        toggle_stream_pause=true, script_F8=true, F8=true}
    -- What the player sees if the game cannot say which key a shortcut is on.
    local KEY_NAMES = {camera_bookmark_save0='Shift+F9', camera_bookmark_save1='Shift+F10',
        camera_bookmark_save2='Shift+F11', camera_bookmark_save3='Shift+F12',
        camera_bookmark_view1='F10', camera_bookmark_view2='F11', camera_bookmark_view3='F12'}
    local hotkey, capturing, capture_token, flash_token = DEFAULT_HOTKEY, false, 0, 0
    local controllers = {}
    local tracked, panel, launcher, status_ui, mode_ui = {}, nil, nil, nil, nil
    local home_x, home_y, drag_x, drag_y, watch_launcher
    local launcher_s, placed, hover, watching, settle, dock_left = 1, false, false, false, 0, 0
    local last_status = '复活默认跟随战役开关；仅作用于玩家控制部队'
    local rate = {0.01, 0.05, 0.125, 0.25}
    -- fatigue_state() values with a stat penalty. Anything else is left alone.
    local fatigued = {threshold_active=true, threshold_winded=true, threshold_tired=true,
        threshold_very_tired=true, threshold_exhausted=true}
    local api = {}
    local general
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
                -- Under the game's AI a unit stops reading as the player's.
                if u:is_player_controlled() or general.owns(u) then result[#result + 1] = u end
            end
        end
        return result
    end
    local GENERAL_MODES = {off='关', all='全军', no_lord='除主将外', reinforcements='仅援军'}
    local GENERAL_DOCTRINES = {auto='自动', attack='进攻', hold='固守', native='原生 AI'}
    local function shortcut_text(key)
        local ok, text = pcall(function() return common.get_localised_string('shortcut_localisation_onscreen_' .. key) end)
        return ok and type(text) == 'string' and text ~= '' and text or nil
    end
    local function hotkey_name(key)
        local ok, text = pcall(function()
            return common.get_context_value('CcoBattleRoot', '', 'ShortcutKey("' .. key .. '")')
        end)
        if ok and type(text) == 'string' and text ~= '' then return text end
        return KEY_NAMES[key] or shortcut_text(key) or key
    end
    local function update_hotkey_button()
        if not key_ui then return end
        key_ui:SetStateText(capturing and '按下新快捷键…' or '快捷键：' .. hotkey_name(hotkey))
        key_ui:SetState(capturing and 'enabled' or 'active')
    end
    local function update_general_buttons()
        if general_ui then
            general_ui:SetStateText('AI 将军：' .. GENERAL_MODES[general.mode()])
            general_ui:SetState(general.mode() == 'off' and 'active' or 'enabled')
        end
        if doctrine_ui then doctrine_ui:SetStateText('战术：' .. GENERAL_DOCTRINES[general.doctrine()]) end
        if general_line then general_line:SetStateText(general.status()) end
    end
    local function update_magic_buttons()
        if magic_ui then magic_ui:SetStateText('无限魔法之风：' .. (magic_on and '开' or '关')); magic_ui:SetState(magic_on and 'enabled' or 'active') end
        if cooldown_ui then cooldown_ui:SetStateText('技能无冷却无限次数：' .. (cooldown_on and '开' or '关')); cooldown_ui:SetState(cooldown_on and 'enabled' or 'active') end
        if silence_ui then silence_ui:SetStateText('禁止敌方技能与召唤：' .. (silence_on and '开' or '关')); silence_ui:SetState(silence_on and 'enabled' or 'active') end
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
    -- An issued order can implicitly take script control. Always release,
    -- even if an order throws; never halt or move units.
    local function command(u, orders)
        local army = u:army()
        local id = army:unique_id()
        local uc = controllers[id]
        if not uc then uc = army:create_unit_controller(); controllers[id] = uc end
        local ok, err = pcall(function()
            uc:clear_all()
            uc:add_units(u)
            orders(uc)
        end)
        local released, release_err = pcall(function() uc:release_control(); uc:clear_all() end)
        if not released then error(release_err) end
        if not ok then error(err) end
    end
    -- Every ability the player can trigger, spells included. The can_perform
    -- check is useless here: it stays true with no uses left (see CA's Malakai
    -- bomb reload), so availability cannot be read and everything is reset.
    local function refresh_abilities()
        local requested = 0
        for _, u in ipairs(units()) do
            if u:is_deployed() and u:number_of_men_alive() > 0 then
                local abilities = {}
                for _, key in pairs(u:owned_non_passive_special_abilities()) do abilities[#abilities + 1] = key end
                if #abilities > 0 then
                    command(u, function(uc)
                        for _, key in ipairs(abilities) do
                            uc:reset_ability_cooldown(key)
                            uc:reset_ability_number_of_uses(key)
                            requested = requested + 1
                        end
                    end)
                end
            end
        end
        return requested
    end
    -- Disabling works per unit, so walking the enemy alliance cannot reach the player
    -- or an allied army. battle:disable_all_abilities would take theirs as well.
    local function silence_unit(entry, u)
        local blocked = 0
        local function block(key)
            if entry.keys[key] then return end
            u:disable_special_ability(key, true)
            entry.keys[key] = true
            blocked = blocked + 1
        end
        for _, key in pairs(u:owned_non_passive_special_abilities()) do block(key) end
        for _, key in pairs(u:owned_passive_special_abilities()) do
            if ENEMY_SUMMONS.passives[key] then block(key) end
        end
        return blocked
    end
    -- Army abilities belong to no unit and nothing disables them, so what they summon
    -- is removed on arrival. Many summons are ordinary unit types: only a unit that
    -- joins after the first pass counts, and a reinforcement army is a real arrival.
    local function silence_enemy()
        local armies = manager:get_non_player_alliance():armies()
        for a = 1, armies:count() do
            local army = armies:item(a)
            local list = army:units()
            for i = 1, list:count() do
                local u = list:item(i)
                if not u:is_player_controlled() then
                    local id = u:unique_ui_id()
                    local entry = silenced[id]
                    if not entry then
                        entry = {unit=u, keys={}, summon=silence_ready and not army:is_reinforcement_army()
                            and ENEMY_SUMMONS.units[u:type()] == true}
                        silenced[id] = entry
                    end
                    silence_blocked = silence_blocked + silence_unit(entry, u)
                    if entry.summon and u:number_of_men_alive() > 0 then
                        command(u, function(uc) uc:kill() end)
                        if not entry.removed then
                            entry.removed = true
                            silence_removed = silence_removed + 1
                        end
                    end
                end
            end
        end
        silence_ready = true
    end
    local function release_enemy()
        for _, entry in pairs(silenced) do
            for key in pairs(entry.keys) do entry.unit:disable_special_ability(key, false) end
        end
        silenced, silence_ready, silence_blocked, silence_removed = {}, false, 0, 0
    end
    -- Perfect vigour freezes fatigue at whatever a stance or ritual started the
    -- battle with, so bring that down once. If the freeze also blocks this call,
    -- lift the attribute for one pass. 0.01: the API divides by it, 0 is forbidden.
    local function clear_fatigue()
        local cleared, stuck = 0, 0
        for _, u in ipairs(units()) do
            local key = u:unique_ui_id()
            local tries = fatigue_tries[key] or 0
            if tries == 2 then u:set_stat_attribute('fatigue_immune', true) end
            if not fatigued[u:fatigue_state()] then
                if tries > 0 then fatigue_cleared[key] = true end
                fatigue_tries[key] = nil
            elseif tries >= 2 then
                fatigue_tries[key] = 3
                stuck = stuck + 1
            elseif u:is_deployed() and u:number_of_men_alive() > 0 then
                if tries == 1 then u:set_stat_attribute('fatigue_immune', false) end
                fatigue_tries[key] = tries + 1
                command(u, function(uc) uc:change_fatigue_amount(0.01) end)
            end
            if fatigue_cleared[key] then cleared = cleared + 1 end
        end
        return cleared, stuck
    end
    local function show_fatigue(text)
        fatigue_text = text
        if fatigue_ui then fatigue_ui:SetStateText(text) end
    end
    function api.fatigue_tick()
        if not allowed() or not fatigue_on or not manager:is_conflict_phase() then return end
        local ok, cleared, stuck = pcall(clear_fatigue)
        if not ok then
            fatigue_on = false
            show_fatigue('清除疲劳已暂停')
            report('清除疲劳已暂停：' .. tostring(cleared))
            return
        end
        show_fatigue('疲劳已清 ' .. cleared .. '队' .. (stuck > 0 and ' · 未清 ' .. stuck .. '队' or ''))
        return cleared, stuck
    end
    function api.silence_tick()
        if not allowed() or not silence_on then return end
        local before = silence_blocked + silence_removed
        local ok, err = pcall(silence_enemy)
        if not ok then
            silence_on = false
            update_magic_buttons()
            report('禁止敌方技能已暂停：' .. tostring(err))
        elseif silence_blocked + silence_removed ~= before then
            report('已禁敌方技能 ' .. silence_blocked .. ' 项 · 清除召唤 ' .. silence_removed .. ' 队')
        end
    end
    function api.magic_tick()
        if not allowed() then return end
        local armies, abilities = 0, 0
        if magic_on then
            local ok, result = pcall(refill_magic)
            if ok then armies = result
            else magic_on = false; report('无限魔法之风已暂停：' .. tostring(result)) end
        end
        if cooldown_on then
            -- Twice a second: this reaches every unit that owns an active ability.
            if ability_wait > 0 then
                ability_wait = ability_wait - 1
            else
                ability_wait = 4
                local ok, result = pcall(refresh_abilities)
                if ok then abilities = result
                else cooldown_on = false; report('技能无冷却无限次数已暂停：' .. tostring(result)) end
            end
        end
        update_magic_buttons()
        return armies, abilities
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
    -- A dot on the unit card shows who answers to the commander. Looked up afresh each
    -- time: cards come and go, and a kept handle to a dead one is a crash.
    local function mark_cards()
        local found, cards = pcall(find_uicomponent, core:get_ui_root(), 'battle_orders', 'cards_panel', 'review_DY')
        if not found or not cards then return end
        for i = 0, cards:ChildCount() - 1 do
            local card = UIComponent(cards:Find(i))
            local id = tonumber(card:Id())
            if id then
                local managed = general.managed(id)
                local mark = card:Find('wh3pt_general_mark')
                if managed and not mark then
                    mark = card:CreateComponent('wh3pt_general_mark', 'ui/wh3pt/card')
                end
                if mark then
                    mark = UIComponent(mark)
                    mark:SetVisible(managed)
                    if managed then
                        mark:SetState(general.paused(id) and 'busy_dot' or 'ok_dot')
                        mark:SetCanResizeWidth(true); mark:SetCanResizeHeight(true)
                        mark:Resize(10, 10)
                        mark:SetDockingPoint(3)
                        mark:SetDockOffset(-3, 3)
                    end
                end
            end
        end
    end
    -- The panel is usually shut when a hotkey is used, so the launcher says what happened.
    local function flash(text)
        if not launcher then return end
        flash_token = flash_token + 1
        local token = flash_token
        launcher:SetStateText(text)
        manager:real_callback(function()
            if token == flash_token and not stopped then launcher:SetStateText(LAUNCHER_TEXT) end
        end, 2500, 'wh3pt_battle_flash')
    end
    local function toggle_selected()
        local given, count = general.toggle_selected()
        local text = given == nil and '先选中部队' or (given and 'AI 接管 ' or '已收回 ') .. count .. ' 队'
        report(text)
        flash(text)
        update_general_buttons()
        mark_cards()
    end
    local function stop_capture(text)
        capturing = false
        update_hotkey_button()
        report(text)
    end
    local function start_capture()
        capturing, capture_token = true, capture_token + 1
        local token = capture_token
        update_hotkey_button()
        report('按下想用的快捷键；Esc 或再点一次取消')
        manager:real_callback(function()
            if capturing and token == capture_token then guard(function() stop_capture('没等到按键，快捷键未改') end) end
        end, 10000, 'wh3pt_battle_capture')
    end
    local function capture(key)
        if key == 'escape_menu' then return stop_capture('快捷键未改') end
        if PANEL_KEYS[key] then return report('F9 用来开面板，换一个键') end
        -- Held camera keys repeat, and each repeat would hand the units back and forth.
        if string.match(key, '^step_') or string.match(key, '^rot_') or string.match(key, '^cam_') then
            return report('这个键用来移动镜头，换一个键')
        end
        hotkey = key
        pcall(function() core:svr_save_registry_string(HOTKEY_SETTING, key) end)
        local original = shortcut_text(key)
        stop_capture('快捷键已改为 ' .. hotkey_name(key)
            .. (original and '，原功能「' .. original .. '」也会触发' or ''))
    end
    -- Shift+F9 is also the game's "save camera 1" and F9 its "view camera 1", so the panel
    -- key would throw the camera back to where the last hotkey press left it. Undone here:
    -- disabling either shortcut could silence the event this all depends on.
    local function keep_camera()
        local camera = manager:camera()
        local position, target = camera:position(), camera:target()
        manager:real_callback(function()
            pcall(function() camera:move_to(position, target, 0, false, -1, false) end)
        end, 60, 'wh3pt_battle_keep_camera')
    end
    function api.general_tick()
        if not allowed() then return end
        general.tick()
        update_general_buttons()
        mark_cards()
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
            if action == 'magic' then magic_on = not magic_on else cooldown_on = not cooldown_on; ability_wait = 0 end
            local enabling = action == 'magic' and magic_on or action == 'cooldown' and cooldown_on
            if not enabling then
                update_magic_buttons()
                report((action == 'magic' and '无限魔法之风' or '技能无冷却无限次数') .. '：已关闭')
                return
            end
            local armies, abilities = api.magic_tick()
            if action == 'magic' and magic_on then report('无限魔法之风：持续补充 ' .. armies .. ' 支玩家军队')
            elseif action == 'cooldown' and cooldown_on then report('技能无冷却无限次数：持续重置 ' .. abilities .. ' 项技能')
            end
            return
        end
        if action == 'silence' then
            silence_on = not silence_on
            if silence_on then
                report('禁止敌方技能：已开启，只作用于敌方')
                api.silence_tick()
            else
                release_enemy()
                report('禁止敌方技能：已关闭')
            end
            update_magic_buttons()
            return
        end
        if action == 'general' or action == 'doctrine' then
            if action == 'general' then general.cycle_mode() else general.cycle_doctrine() end
            update_general_buttons()
            report(action == 'general' and 'AI 将军：' .. GENERAL_MODES[general.mode()]
                or '战术：' .. GENERAL_DOCTRINES[general.doctrine()])
            return
        end
        if action == 'general_pick' then return toggle_selected() end
        if action == 'general_key' then
            if capturing then return stop_capture('快捷键未改') end
            return start_capture()
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
    -- The panel hangs under the launcher, wherever the player dragged that to.
    local function place_panel()
        local w, h = core:get_ui_root():Dimensions()
        local lw, lh = launcher:Dimensions()
        local pw, ph = panel:Dimensions()
        local below = home_y + lh + 4
        panel:MoveTo(math.floor(math.max(0, math.min(w - pw, home_x + lw - pw))),
            math.floor(below + ph <= h and below or math.max(0, home_y - ph - 4)))
    end
    local function toggle_panel()
        if not panel then return end
        local open = panel:Visible(true)
        if not open then place_panel() end
        panel:SetVisible(not open)
    end
    function api.shortcut(key)
        if capturing then
            if PANEL_KEYS[key] and hotkey == DEFAULT_HOTKEY then pcall(keep_camera) end
            return capture(key)
        end
        if PANEL_KEYS[key] then
            toggle_panel()
            if hotkey == DEFAULT_HOTKEY then pcall(keep_camera) end
        elseif key == hotkey and allowed() then
            toggle_selected()
        end
    end
    -- Until the player places it the launcher sits left of the bar behind the speed
    -- buttons, which reaches further than the radar. Only the bar's size is read:
    -- the HUD slides in from off screen, so its position means nothing at first.
    local function dock_launcher()
        if placed or not launcher then return end
        local root = core:get_ui_root()
        local w = root:Dimensions()
        local lw = launcher:Dimensions()
        local found, bar = pcall(find_uicomponent, root, 'hud_battle', 'radar_holder', 'bar_small_top')
        local clear = found and bar and bar:Dimensions() or 400 * launcher_s
        local x = math.floor(math.max(0, w - clear - 8 * launcher_s - lw))
        if x ~= home_x then
            launcher:MoveTo(x, home_y)
            home_x = x
            place_panel()
        end
    end
    -- The HUD is still settling when the battle script starts, so look again for a while.
    local function keep_docking()
        local running = dock_left > 0
        dock_left = 10
        if running then return end
        local function tick()
            if stopped or placed or dock_left <= 0 then dock_left = 0; return end
            dock_left = dock_left - 1
            guard(dock_launcher)
            manager:real_callback(tick, 1000, 'wh3pt_battle_dock')
        end
        tick()
    end
    -- A drag ends in a click; only a press that stayed put opens the panel.
    local function drop_launcher()
        local x, y = launcher:Position()
        if drag_x then x, y = drag_x, drag_y end
        drag_x, drag_y, settle = nil, nil, 5
        if math.abs(x - home_x) + math.abs(y - home_y) <= 6 then return false end
        home_x, home_y, placed = x, y, true
        core:svr_save_string('wh3pt_battle_launcher', math.floor(x) .. ',' .. math.floor(y))
        place_panel()
        return true
    end
    -- Runs only around the mouse: a timer must not outlive the UI it reads.
    local function start_watching()
        if watching or stopped or not launcher then return end
        watching = true
        manager:real_callback(function() guard(watch_launcher) end, 100, 'wh3pt_battle_watch')
    end
    -- The engine hands a dropped button back to where the drag began, as it does with
    -- unit cards, so follow the drag and hold the button where it was let go.
    watch_launcher = function()
        watching = false
        if stopped then return end
        if launcher:IsDragged() then
            drag_x, drag_y = launcher:Position()
        else
            if drag_x then drop_launcher() end
            if settle > 0 then
                settle = settle - 1
                local x, y = launcher:Position()
                if x ~= home_x or y ~= home_y then launcher:MoveTo(home_x, home_y) end
            end
        end
        if hover or drag_x or settle > 0 then start_watching() end
    end
    function api.ui()
        if panel or not allowed() then return end
        local root = core:get_ui_root()
        local w, h = root:Dimensions()
        -- Same probe as the campaign panel: these ten glyphs measure 140 at 100%.
        local measured, ratio = pcall(function()
            local probe = UIComponent(root:CreateComponent('wh3pt_battle_probe', 'ui/wh3pt/label'))
            local read, width = pcall(function()
                probe:SetCanResizeWidth(true); probe:SetCanResizeHeight(true)
                probe:Resize(math.max(800, w), 80)
                probe:SetState('probe')
                probe:SetStateText('战锤修改器界面校准用')
                return (probe:TextDimensions())
            end)
            probe:Destroy()
            assert(read and type(width) == 'number' and width > 0)
            return width / 140
        end)
        local s = math.max(1, math.min(2, measured and ratio or math.min(w / 1280, h / 720)))
        local ox, oy = 0, 0
        local function make(parent, id, template, dx, dy, width, height, text, state)
            local c = UIComponent(parent:CreateComponent('wh3pt_battle_' .. id, 'ui/wh3pt/' .. template))
            c:SetDockingPoint(0)
            if c.SetDockOffset then c:SetDockOffset(0, 0) end
            c:SetCanResizeWidth(true); c:SetCanResizeHeight(true)
            c:Resize(math.floor(width * s), math.floor(height * s))
            c:MoveTo(math.floor(ox + dx * s), math.floor(oy + dy * s))
            c:SetState(state or 'active')
            if text then c:SetStateText(text) end
            c:PropagatePriority(210)
            return c
        end
        local lw, lh = 112, 26
        local saved_x, saved_y = string.match(core:svr_load_string('wh3pt_battle_launcher') or '', '^(%d+),(%d+)$')
        launcher_s, placed = s, saved_x ~= nil
        ox = math.max(0, math.min(w - lw * s, tonumber(saved_x) or 0))
        oy = math.max(0, math.min(h - lh * s, tonumber(saved_y) or 6 * s))
        launcher = make(root, 'open', 'button', 0, 0, lw, lh, LAUNCHER_TEXT, 'accent')
        home_x, home_y = launcher:Position()
        if launcher.SetMoveable then pcall(function() launcher:SetMoveable(true) end) end
        panel = make(root, 'panel', 'card', 0, 0, 336, 262, '', 'panel_bg')
        dock_launcher()
        place_panel()
        ox, oy = panel:Position()
        make(panel, 'title', 'label', 12, 8, 96, 24, '战斗修改器')
        fatigue_ui = make(panel, 'fatigue', 'label', 110, 8, 184, 24, fatigue_text, 'hint')
        fatigue_ui:SetTooltipText('完美活力开启时生效：开战后清除部队带入战斗的疲劳。显示“未清”说明游戏拒绝了修改。', true)
        make(panel, 'close', 'button', 298, 6, 28, 26, '×')
        mode_ui = make(panel, 'mode', 'button', 10, 36, 155, 26, '持续复活：跟随战役', 'accent')
        make(panel, 'revive', 'button', 171, 36, 155, 26, '立即复活并补满生命')
        make(panel, 'heal', 'button', 10, 66, 155, 26, '治疗存活成员')
        make(panel, 'ammo', 'button', 171, 66, 155, 26, '补满弹药')
        magic_ui = make(panel, 'magic', 'button', 10, 96, 155, 26, '')
        cooldown_ui = make(panel, 'cooldown', 'button', 171, 96, 155, 26, '')
        silence_ui = make(panel, 'silence', 'button', 10, 126, 316, 26, '')
        general_ui = make(panel, 'general', 'button', 10, 156, 155, 26, '')
        doctrine_ui = make(panel, 'doctrine', 'button', 171, 156, 155, 26, '')
        pick_ui = make(panel, 'general_pick', 'button', 10, 186, 155, 26, '托管 / 收回选中部队')
        key_ui = make(panel, 'general_key', 'button', 171, 186, 155, 26, '')
        general_line = make(panel, 'general_line', 'label', 12, 214, 312, 22, '', 'hint')
        update_magic_buttons()
        update_general_buttons()
        update_hotkey_button()
        mode_ui:SetTooltipText('点击在 跟随战役 / 开 / 关 之间切换；复活以开战人数为上限，仅手动战斗。', true)
        magic_ui:SetTooltipText('继承战役设置，可在本场独立切换；每0.1秒补充玩家军队魔法池至至少30、储备至至少100。关闭后停止补充。', true)
        cooldown_ui:SetTooltipText('继承战役设置，可在本场独立切换；每0.5秒重置玩家部队全部主动技能与法术的冷却和使用次数。仍需施法动作和有效目标，法术仍消耗魔法。', true)
        silence_ui:SetTooltipText('继承战役设置，可在本场独立切换；只作用于敌方。每秒禁用敌方单位的法术、主动技能和会触发召唤的被动，召唤出的部队到场即清除。军队技能无法禁用，炮击和增益类军队技能仍可能释放。关闭后恢复敌方技能。', true)
        general_ui:SetTooltipText('点击在 关 / 全军 / 除主将外 / 仅援军 之间切换。部队仍在你手里：随时可以下令，你下的命令优先，15 秒内 AI 不再插手这支部队。', true)
        doctrine_ui:SetTooltipText('自动：按敌我强弱决定固守或推进。进攻：立刻列阵推进。固守：只打靠近的敌人。原生 AI：交给游戏自带 AI，部队卡牌变灰；城墙攻防战默认用它。', true)
        pick_ui:SetTooltipText('单独交出或收回选中的部队，不受上面的模式限制，和快捷键效果相同。卡牌右上角绿点表示 AI 指挥中，黄点表示你刚接管。', true)
        key_ui:SetTooltipText('点一下，再按下想用的快捷键。Esc 或再点一次取消。只能用游戏里绑了功能的键，它原来的功能也会触发；想用没被占用的键，先在 选项 → 控制 里把它绑给一个空闲项（如 保存视角 5）。', true)
        status_ui = make(panel, 'status', 'label', 12, 236, 312, 22, last_status, 'hint')
        status_ui:SetTooltipText(last_status, true)
        panel:SetVisible(false)
        keep_docking()
    end
    function api.start()
        if started then return false end
        local pending = core:svr_load_bool('wh3pt_magic_pending')
        core:svr_save_bool('wh3pt_magic_pending', false)
        if not allowed() then return false end
        started = true
        local loaded, saved_key = pcall(function() return core:svr_load_registry_string(HOTKEY_SETTING) end)
        if loaded and type(saved_key) == 'string' and saved_key ~= '' then hotkey = saved_key end
        guard(function()
            if not pending then return end
            local owner = core:svr_load_string('wh3pt_magic_owner')
            local armies = manager:get_player_alliance():armies()
            for i = 1, armies:count() do
                local army = armies:item(i)
                if army:is_player_controlled() and army:faction_key() == owner then
                    magic_on = core:svr_load_bool('wh3pt_magic_enabled') == true
                    cooldown_on = core:svr_load_bool('wh3pt_cooldown_enabled') == true
                    silence_on = core:svr_load_bool('wh3pt_silence_enabled') == true
                    if core:svr_load_bool('wh3pt_general_enabled') == true then general.set_mode('all') end
                    fatigue_on = core:svr_load_bool('wh3pt_vigour_enabled') == true
                    if fatigue_on then show_fatigue('开战后清除疲劳') end
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
                or c.string == 'wh3pt_battle_silence' or c.string == 'wh3pt_battle_general'
                or c.string == 'wh3pt_battle_doctrine' or c.string == 'wh3pt_battle_general_pick'
                or c.string == 'wh3pt_battle_general_key'
        end, function(c) guard(function()
            if c.string == 'wh3pt_battle_open' then
                if not drop_launcher() then toggle_panel() end
                start_watching()
            elseif c.string == 'wh3pt_battle_close' then toggle_panel()
            else api.action(string.gsub(c.string, '^wh3pt_battle_', '')) end
        end) end, true)
        core:add_listener('wh3pt_battle_hotkey', 'ShortcutTriggered', true, function(c)
            guard(function() api.shortcut(c.string) end)
        end, true)
        core:add_listener('wh3pt_battle_mouse_on', 'ComponentMouseOn', function(c)
            return c.string == 'wh3pt_battle_open'
        end, function() hover = true; guard(start_watching) end, true)
        core:add_listener('wh3pt_battle_mouse_off', 'ComponentMouseOff', function(c)
            return c.string == 'wh3pt_battle_open'
        end, function() hover = false end, true)
        core:add_listener('wh3pt_battle_dock_loaded', 'LoadingScreenDismissed', true, function() guard(keep_docking) end, true)
        manager:register_phase_change_callback('Deployed', function() guard(keep_docking) end)
        manager:real_callback(function() guard(api.ui) end, 1000, 'wh3pt_battle_ui')
        manager:repeat_callback(function()
            guard(function() api.tick(final_phase) end)
            guard(api.fatigue_tick)
            guard(api.silence_tick)
        end, 1000, 'wh3pt_battle_revival')
        manager:repeat_callback(function() guard(api.magic_tick) end, 100, 'wh3pt_battle_magic')
        manager:repeat_callback(function() guard(api.general_tick) end, GENERAL_TUNE.tick, 'wh3pt_battle_general')
        for _, command_name in ipairs({'Move', 'Move Orientation Width', 'Move Rotation Angle', 'Attack Unit',
                'Attack Building', 'Climb/Dock Building', 'Special Ability', 'Halt', 'Withdraw'}) do
            manager:register_command_handler_callback(command_name, function() guard(general.player_order) end,
                'wh3pt_general_' .. command_name)
        end
        manager:register_phase_change_callback('VictoryCountdown', function()
            final_phase = true
            general.victory()
            guard(function() api.tick(true) end)
        end)
        manager:register_phase_change_callback('Complete', function()
            stopped = true
            manager:remove_process('wh3pt_battle_revival')
            manager:remove_process('wh3pt_battle_magic')
            manager:remove_process('wh3pt_battle_general')
            guard(general.stop)
            magic_on, cooldown_on, fatigue_on, silence_on, controllers = false, false, false, false, {}
            if panel then panel:SetVisible(false) end
        end)
        log('Started: revival timer 1000ms; magic timer 100ms; F9 panel registered')
        return true
    end
    function api.status() return last_status, mode end
    function api.magic_state() return magic_on, cooldown_on end
    function api.fatigue_state() return fatigue_on, fatigue_text end
    function api.silence_state() return silence_on, silence_blocked, silence_removed end
    function api.hotkey() return hotkey, capturing end
    general = new_general(manager, {data=GENERAL_DATA, log=log, report=report,
        free_cooldowns=function() return cooldown_on end,
        walled=function()
            return manager:is_siege_battle() and not string.find(tostring(manager:battle_type()), 'unfortified')
        end})
    api.general = general
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
