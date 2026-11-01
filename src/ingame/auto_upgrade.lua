-- Upgrade only what a click on the native card would upgrade. The game's own slot
-- checks decide and its slot command starts the construction, so cost, build time
-- and requirements stay native. Settlements only: horde armies are left alone.
local function create_auto_upgrade(env)
    local api, enabled, active, generation = {}, false, false, 0
    local ATTEMPTS = 5
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
    local function log(text) (ModLog or out)('[WH3PT upgrade] ' .. text) end
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
    -- and key of the one option, provided it is also what the slot command would pick.
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
        for i = 0, regions:num_items() - 1 do
            local cqi = regions:item_at(i):cqi()
            if settlement_open(cqi) then
                local count = ask(cqi, 'BuildingSlotList.Size')
                assert(type(count) == 'number', '建筑格子读取失败')
                for index = 0, count - 1 do
                    local slot = 'BuildingSlotList.At(' .. index .. ')'
                    if slot_free(cqi, slot) then
                        local level, key = only_option(cqi, slot)
                        if level then found[#found + 1] = {cqi = cqi, slot = slot, level = level, key = key} end
                    end
                end
            end
        end
        return found
    end
    -- Asked right before the command: the upgrade before it has already been paid for
    -- and may have used up a province's building cap.
    local function startable(item)
        local cqi, argument, level = item.cqi, '(' .. item.slot .. ')', item.level .. '.'
        return settlement_open(cqi) and slot_free(cqi, item.slot) and select(2, only_option(cqi, item.slot)) == item.key
            and all(cqi, level, {'IsActiveForBuildingBrowser' .. argument, 'CanBuildInCurrentSlot' .. argument,
                'HasEnoughDevelopmentPoints(this)', 'CanAfford(this)', 'CanAffordResourceCostForSlot' .. argument}, true)
            and all(cqi, level, {'IsEpisodicRestricted' .. argument, 'DoesBuildingExceedCap' .. argument,
                'IsLockedByLevel' .. argument, 'IsLockedByAnyTech'}, false)
    end
    -- A zero-turn construction finishes at once and never reads as upgrading.
    local function started(item)
        return ask(item.cqi, item.slot .. '.IsUpgrading') == true
            or ask(item.cqi, item.slot .. '.BuildingContext.BuildingLevelRecordContext.Key') == item.key
    end
    local function finish(run)
        active = false
        if run.done + run.failed > 0 then
            local parts = {'建筑自动升级：已开始 ' .. run.done .. ' 项'}
            local spent = run.treasury - env.faction():treasury()
            if spent > 0 then parts[1] = parts[1] .. '，花费 ' .. spent .. ' 金' end
            if run.blocked > 0 then parts[#parts + 1] = run.blocked .. ' 项暂不满足条件' end
            if run.failed > 0 then parts[#parts + 1] = run.failed .. ' 项未生效，详见 mod 日志' end
            env.message(table.concat(parts, '；'), run.failed > 0)
            env.render()
        elseif run.announce then
            env.message('建筑自动升级已开：' .. (run.blocked > 0 and (run.blocked .. ' 项暂不满足条件') or '当前没有符合条件的建筑'))
        end
    end
    local step
    -- Issued one at a time: the command only queues, so a second one sent now would
    -- still be judged against the money the first is about to spend.
    local function await(run, item, tries)
        env.callback(function()
            if run.token ~= generation then return end
            local ok = env.guard(function()
                if started(item) then
                    run.done = run.done + 1
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
            if startable(item) then
                common.call_context_command('CcoCampaignSettlement', item.cqi, item.slot .. '.UpgradeToFirstAvailableOption')
                return await(run, item, ATTEMPTS)
            end
            run.blocked = run.blocked + 1
        end
        finish(run)
    end
    function api.enabled() return enabled end
    function api.check(announce)
        -- Commands only reach the game on the player's own turn.
        if not enabled or active or not cm:is_local_players_turn(true) then return end
        local items = candidates()
        generation = generation + 1
        local run = {token = generation, items = items, next = 1, done = 0, blocked = 0, failed = 0,
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
