-- Upgrade only what a click on the native card would upgrade. The game's own slot
-- checks decide, read the way the building browser reads them, and the documented
-- script call queues the construction, so cost, build time and requirements stay
-- native. Settlements only: horde armies are left alone.
local function create_auto_upgrade(env)
    local api, enabled, active, generation = {}, false, false, 0
    local ATTEMPTS = 5
    -- Every run is written down: a turn start passes unseen, and a battle empties
    -- lua_mod_log.txt before anyone can read it.
    local trace = env.journal and env.journal('wh3pt_upgrade.log', 1024 * 1024)
    local function log(text)
        (ModLog or out)('[WH3PT upgrade] ' .. text)
        if trace then trace(text) end
    end
    -- Everything is read from the settlement. Arguments resolve against that root, not
    -- against the object they are passed to, so `this` is the settlement and a slot
    -- argument has to spell out its whole path.
    local function ask(cqi, expression)
        return common.get_context_value('CcoCampaignSettlement', cqi, expression)
    end
    -- An unreadable value fails the comparison, which leaves the building alone.
    local function all(cqi, prefix, names, wanted)
        for _, name in ipairs(names) do
            if ask(cqi, prefix .. name) ~= wanted then return false end
        end
        return true
    end
    local function settlement_open(cqi)
        return ask(cqi, 'IsPlayerOwned') == true and all(cqi, '', {'IsUnderSiege', 'IsAbandoning'}, false)
    end
    -- The conditions of the native upgrade arrow. Lifecycle buildings (Nurgle) have no
    -- arrow, they advance on their own.
    local function slot_free(cqi, slot)
        return all(cqi, slot .. '.', {'CanUpgrade', 'IsActive', 'IsPlayerFaction', 'IsRegionSlot'}, true)
            and all(cqi, slot .. '.', {'IsEmpty', 'IsBuildingNew', 'IsUpgrading', 'IsConverting',
                'IsRepairing', 'IsDismantling', 'CanRepair', 'CanConvert'}, false)
            and ask(cqi, 'IsContextValid(' .. slot .. '.LifecycleStateContext)') == false
    end
    -- Direct upgrades this settlement could ever build. A branch that is only locked by
    -- a technology for now is still the player's choice, so it counts. Returns the path
    -- and key of the one option, provided it is also what the native list offers first.
    local function only_option(cqi, slot)
        local upgrades = slot .. '.BuildingContext.BuildingLevelRecordContext.UpgradesList'
        local count, level = ask(cqi, upgrades .. '.Size'), nil
        if type(count) ~= 'number' then return nil end
        for i = 0, count - 1 do
            local option = upgrades .. '.At(' .. i .. ')'
            if ask(cqi, 'BuildingCanEverBeBuilt(' .. option .. ', ' .. slot .. ')') == true then
                if level then return nil end
                level = option
            end
        end
        if not level then return nil end
        local key = ask(cqi, level .. '.Key')
        if type(key) == 'string' and key ~= '' and ask(cqi, slot .. '.PossibleUpgradeList(true).FirstContext.Key') == key then
            return level, key
        end
    end
    local function candidates()
        local regions, found = env.faction():region_list(), {}
        local seen = {regions = regions:num_items(), settlements = 0, slots = 0}
        for i = 0, regions:num_items() - 1 do
            local region = regions:item_at(i)
            local cqi = region:cqi()
            if settlement_open(cqi) then
                seen.settlements = seen.settlements + 1
                local count = ask(cqi, 'BuildingSlotList.Size')
                assert(type(count) == 'number', '建筑格子读取失败')
                for index = 0, count - 1 do
                    local slot = 'BuildingSlotList.At(' .. index .. ')'
                    if slot_free(cqi, slot) then
                        seen.slots = seen.slots + 1
                        local level, key = only_option(cqi, slot)
                        if level then
                            found[#found + 1] = {cqi = cqi, region = region, index = index, slot = slot,
                                level = level, key = key, built = ask(cqi, slot .. '.BuildingContext.BuildingLevelRecordContext.Key')}
                        end
                    end
                end
            end
        end
        return found, seen
    end
    -- Asked right before the command: the upgrade before it has already been paid for
    -- and may have used up a province's building cap. Names the first condition that
    -- fails, the same ones the building browser shows.
    local function blocker(item)
        local cqi, argument, level = item.cqi, '(' .. item.slot .. ')', item.level .. '.'
        if not settlement_open(cqi) then return 'settlement' end
        if not slot_free(cqi, item.slot) then return 'slot' end
        if select(2, only_option(cqi, item.slot)) ~= item.key then return 'options' end
        for _, name in ipairs({'IsActiveForBuildingBrowser' .. argument, 'CanBuildInCurrentSlot' .. argument,
                'HasEnoughDevelopmentPoints(this)', 'CanAfford(this)', 'CanAffordResourceCostForSlot' .. argument}) do
            if ask(cqi, level .. name) ~= true then return name end
        end
        for _, name in ipairs({'IsEpisodicRestricted' .. argument, 'DoesBuildingExceedCap' .. argument,
                'IsLockedByLevel' .. argument, 'IsLockedByAnyTech'}) do
            if ask(cqi, level .. name) ~= false then return name end
        end
    end
    -- The script call takes the slot itself. The settlement's slot list is the one the
    -- context counts in, but the building standing there has to agree before anything
    -- is queued; failing that, the one slot holding that building.
    local function script_slot(item)
        local slots = item.region:slot_list()
        local function holds(slot)
            return slot and not slot:is_null_interface() and slot:has_building() and slot:building():name() == item.built
        end
        if item.index < slots:num_items() and holds(slots:item_at(item.index)) then return slots:item_at(item.index) end
        local found
        for i = 0, slots:num_items() - 1 do
            if holds(slots:item_at(i)) then
                if found then return nil end
                found = slots:item_at(i)
            end
        end
        return found
    end
    -- A zero-turn construction finishes at once and never reads as upgrading.
    local function started(item)
        return ask(item.cqi, item.slot .. '.IsUpgrading') == true
            or ask(item.cqi, item.slot .. '.BuildingContext.BuildingLevelRecordContext.Key') == item.key
    end
    local function finish(run)
        active = false
        local spent = run.treasury - env.faction():treasury()
        log('run: ' .. run.seen.settlements .. ' of ' .. run.seen.regions .. ' settlements open, '
            .. run.seen.slots .. ' slots free, ' .. #run.items .. ' single upgrades; started ' .. run.done
            .. ', waiting ' .. run.blocked .. ', failed ' .. run.failed .. ', spent ' .. spent)
        if run.done + run.failed > 0 then
            local parts = {'建筑自动升级：已开始 ' .. run.done .. ' 项'}
            if spent > 0 then parts[1] = parts[1] .. '，花费 ' .. spent .. ' 金' end
            if run.blocked > 0 then parts[#parts + 1] = run.blocked .. ' 项暂不满足条件' end
            if run.failed > 0 then parts[#parts + 1] = run.failed .. ' 项未生效，详见 wh3pt_upgrade.log' end
            env.message(table.concat(parts, '；'), run.failed > 0)
            env.render()
        elseif run.announce then
            env.message('建筑自动升级已开：' .. (run.blocked > 0 and (run.blocked .. ' 项暂不满足条件') or '当前没有符合条件的建筑'))
        end
    end
    local step
    -- Issued one at a time: the call only queues, so a second one sent now would
    -- still be judged against the money the first is about to spend.
    local function await(run, item, tries)
        env.callback(function()
            if run.token ~= generation then return end
            local ok = env.guard(function()
                if started(item) then
                    run.done = run.done + 1
                    log('started: ' .. item.key .. ' in ' .. tostring(item.cqi) .. ' ' .. item.slot)
                elseif tries > 1 then
                    return await(run, item, tries - 1)
                else
                    run.failed = run.failed + 1
                    log('no construction started: ' .. item.key .. ' in ' .. tostring(item.cqi) .. ' ' .. item.slot)
                end
                step(run)
            end)
            if not ok then active = false end
        end, 0.1)
    end
    step = function(run)
        while run.next <= #run.items do
            local item = run.items[run.next]
            run.next = run.next + 1
            local reason = blocker(item)
            local slot = not reason and script_slot(item) or nil
            if reason then
                run.blocked = run.blocked + 1
                log('waits: ' .. item.key .. ' in ' .. tostring(item.cqi) .. ' ' .. item.slot .. ', ' .. reason)
            elseif not slot then
                run.failed = run.failed + 1
                log('no script slot holds ' .. tostring(item.built) .. ' in ' .. tostring(item.cqi) .. ' ' .. item.slot)
            else
                local ok, err = pcall(function() cm:add_building_to_settlement_queue(slot, item.key) end)
                if ok then
                    log('queued: ' .. item.key .. ' in ' .. tostring(item.cqi) .. ' ' .. item.slot)
                    return await(run, item, ATTEMPTS)
                end
                run.failed = run.failed + 1
                log('queue refused ' .. item.key .. ': ' .. tostring(err))
            end
        end
        finish(run)
    end
    function api.enabled() return enabled end
    function api.check(announce)
        -- Commands only reach the game on the player's own turn.
        if not enabled or active or not cm:is_local_players_turn(true) then return end
        local items, seen = candidates()
        generation = generation + 1
        local run = {token = generation, items = items, seen = seen, next = 1, done = 0, blocked = 0, failed = 0,
            treasury = env.faction():treasury(), announce = announce}
        active = true
        if not env.guard(function() step(run) end) then active = false end
    end
    function api.toggle(value)
        env.faction()
        enabled = value == nil and not enabled or value == true
        cm:set_saved_value(env.setting('auto_upgrade', 'enabled'), enabled)
        generation, active = generation + 1, false
        env.message(enabled and '建筑自动升级已开：每回合开始检查一次' or '建筑自动升级已关闭；已开始的建造不受影响')
        -- A click means now. An explicit value is an undo, which only restores the switch.
        if enabled and value == nil then api.check(true) end
    end
    function api.reset()
        generation, active = generation + 1, false
        enabled = cm:get_saved_value(env.setting('auto_upgrade', 'enabled')) == true
    end
    core:remove_listener('wh3pt_auto_upgrade_turn')
    core:add_listener('wh3pt_auto_upgrade_turn', 'FactionTurnStart', true, function(c)
        env.guard(function()
            if enabled and c:faction():name() == env.faction():name() then
                -- Let finished buildings and the new income land first.
                env.callback(function() env.guard(function() api.check() end) end, 0.5)
            end
        end)
    end, true)
    api.reset()
    return api
end
