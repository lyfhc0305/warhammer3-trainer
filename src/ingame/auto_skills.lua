-- Spends the skill points of the player's lords and heroes. The game's own add_skill
-- checks rank, parent skills and points as a click in the skill tree would, and
-- spends a real point; this file only picks the skill. Choices between options
-- (mounts, lores, god blessings) stay with the player, and the skills CA's AI calls
-- useless come last.
local function create_auto_skills(env)
    local api, enabled, running, waiting, again, generation = {}, false, false, false, false, 0
    local data = env.data or {}
    local exclusive, useless = data.exclusive or {}, data.useless or {}
    -- A character whose picks keep failing is skipped; a run never loops forever.
    local MAX_FAILURES, MAX_STEPS = 3, 400
    -- Runs happen at turn start and after battles, when lua_mod_log.txt is about to be
    -- emptied; the file keeps what they did.
    local trace = env.journal and env.journal('wh3pt_skills.log', 1024 * 1024)
    local function log(text)
        (ModLog or out)('[WH3PT skills] ' .. text)
        if trace then trace(text) end
    end
    -- Characters are read through their native context, by cqi, the way CA's own
    -- scripts read a faction. Anything unreadable comes back as nil.
    local function ask(cqi, expression)
        if type(common) ~= 'table' or type(common.get_context_value) ~= 'function' then return nil end
        local ok, value = pcall(common.get_context_value, 'CcoCampaignCharacter', cqi, expression)
        if ok then return value end
    end
    -- Skills that can take a point now, best first: useful before useless, then left
    -- to right and top to bottom as the tree is drawn.
    local function candidates(cqi, subtype, refused)
        local count = ask(cqi, 'SkillList.Size')
        if type(count) ~= 'number' then return nil end
        local found, statuses, choices, skipped = {}, {}, exclusive[subtype] or {}, 0
        for i = 0, count - 1 do
            local path = 'SkillList.At(' .. i .. ')'
            local status = ask(cqi, path .. '.Status')
            statuses[tostring(status)] = (statuses[tostring(status)] or 0) + 1
            if status == 'available' then
                local key = ask(cqi, path .. '.Key')
                local level, levels = ask(cqi, path .. '.Level'), ask(cqi, path .. '.TotalLevels')
                if type(key) == 'string' and choices[key] then
                    skipped = skipped + 1
                elseif type(key) == 'string' and key ~= '' and not refused[key]
                    and ask(cqi, path .. '.IsBackgroundSkill') == false
                    and type(level) == 'number' and type(levels) == 'number' and level < levels then
                    found[#found + 1] = {key = key, useless = useless[key] == true,
                        x = tonumber(ask(cqi, path .. '.Indent')) or 0, y = tonumber(ask(cqi, path .. '.Tier')) or 0}
                end
            end
        end
        table.sort(found, function(a, b)
            if a.useless ~= b.useless then return b.useless end
            if a.x ~= b.x then return a.x < b.x end
            if a.y ~= b.y then return a.y < b.y end
            return a.key < b.key
        end)
        return found, statuses, skipped
    end
    local function describe(statuses)
        local parts = {}
        for status, count in pairs(statuses) do parts[#parts + 1] = status .. '=' .. count end
        table.sort(parts)
        return table.concat(parts, ' ')
    end
    local function finish(run)
        running = false
        log('run: ' .. #run.queue .. ' characters; learned ' .. run.learned .. ' for ' .. run.characters
            .. ', left to the player ' .. run.left .. ', failed ' .. run.failed .. ', unreadable ' .. run.unread)
        if run.learned > 0 or run.failed > 0 then
            local parts = {'自动加点：' .. run.characters .. ' 名角色学习了 ' .. run.learned .. ' 个技能'}
            if run.left > 0 then parts[#parts + 1] = run.left .. ' 名角色的剩余点数只能用在多选一或未解锁的技能上，留给你' end
            if run.failed > 0 then parts[#parts + 1] = run.failed .. ' 次未生效，详见 wh3pt_skills.log' end
            env.message(table.concat(parts, '；'), run.failed > 0)
            env.render()
        elseif run.announce then
            if run.unread > 0 then
                env.message('自动加点已开，但读不到角色的技能树，详见 wh3pt_skills.log', true)
            elseif run.left > 0 then
                env.message('自动加点已开：' .. run.left .. ' 名角色只剩多选一或未解锁的技能，留给你')
            else
                env.message('自动加点已开：现在没有待分配的技能点')
            end
        end
        if again then
            again = false
            api.soon(0.1)
        end
    end
    local step
    -- add_skill lands between ticks; the point is judged spent only once it is gone.
    local function await(run, item, key, points)
        env.callback(function()
            if run.token ~= generation then return end
            local ok = env.guard(function()
                local now = ask(item.cqi, 'SkillPointsAvailable')
                if type(now) == 'number' and now < points then
                    item.learned = item.learned + 1
                    run.learned = run.learned + 1
                else
                    item.refused[key] = true
                    item.failures = item.failures + 1
                    run.failed = run.failed + 1
                    log('not learned: ' .. key .. ' for character ' .. tostring(item.cqi)
                        .. ', points ' .. tostring(points) .. ' -> ' .. tostring(now))
                end
                step(run)
            end)
            if not ok then running = false end
        end, 0.1)
    end
    step = function(run)
        while run.next <= #run.queue and run.steps < MAX_STEPS do
            local item = run.queue[run.next]
            local ch = cm:get_character_by_cqi(item.cqi)
            local valid = ch and not ch:is_null_interface() and ch:faction():name() == run.faction
            local points = valid and ask(item.cqi, 'SkillPointsAvailable') or nil
            if valid and type(points) ~= 'number' then
                run.unread = run.unread + 1
                log('cannot read the skill points of character ' .. tostring(item.cqi))
            elseif valid and points > 0 and item.failures < MAX_FAILURES
                -- Picks the player has made but not confirmed in the skill tree win.
                and ask(item.cqi, 'HasUncommitedSkills') == false then
                local list, statuses, skipped = candidates(item.cqi, item.subtype, item.refused)
                if not list then
                    run.unread = run.unread + 1
                    log('cannot read the skill tree of character ' .. tostring(item.cqi))
                elseif list[1] then
                    run.steps = run.steps + 1
                    local key = list[1].key
                    local ok, err = pcall(function() cm:add_skill(ch, key, false, false) end)
                    log((ok and 'add_skill ' or 'add_skill failed ') .. key .. ' for character ' .. tostring(item.cqi)
                        .. (ok and '' or (': ' .. tostring(err))))
                    return await(run, item, key, points)
                else
                    run.left = run.left + 1
                    log('character ' .. tostring(item.cqi) .. ' keeps ' .. points .. ' points; '
                        .. skipped .. ' choices left to the player; ' .. describe(statuses))
                end
            end
            if item.learned > 0 then run.characters = run.characters + 1 end
            run.next = run.next + 1
        end
        if run.steps >= MAX_STEPS then log('stopped after ' .. MAX_STEPS .. ' skills') end
        finish(run)
    end
    function api.enabled() return enabled end
    function api.check(announce)
        if not enabled then return end
        if running then again = true; return end
        -- Points are spent on the player's own turn, as a click would be.
        if not cm:is_local_players_turn(true) then return end
        local f, queue = env.faction(), {}
        local list = f:character_list()
        for i = 0, list:num_items() - 1 do
            local ch = list:item_at(i)
            if not ch:is_null_interface() then
                queue[#queue + 1] = {cqi = ch:cqi(), subtype = ch:character_subtype_key(),
                    refused = {}, failures = 0, learned = 0}
            end
        end
        generation = generation + 1
        running = true
        local run = {token = generation, queue = queue, next = 1, faction = f:name(), steps = 0,
            learned = 0, failed = 0, characters = 0, left = 0, unread = 0, announce = announce}
        if not env.guard(function() step(run) end) then running = false end
    end
    -- Rank-ups arrive in bursts after a battle; one run picks them all up.
    function api.soon(delay)
        if waiting or not enabled then return end
        waiting = true
        env.callback(function()
            waiting = false
            env.guard(function() api.check() end)
        end, delay)
    end
    function api.toggle(value)
        env.faction()
        enabled = value == nil and not enabled or value == true
        cm:set_saved_value(env.setting('auto_skills', 'enabled'), enabled)
        generation, running, waiting, again = generation + 1, false, false, false
        env.message(enabled and '自动加点已开：回合开始和角色升级时分配技能点' or '自动加点已关闭；已学的技能保留')
        -- A click means now. An explicit value is an undo, which only restores the switch.
        if enabled and value == nil then api.check(true) end
    end
    function api.reset()
        generation, running, waiting, again = generation + 1, false, false, false
        enabled = cm:get_saved_value(env.setting('auto_skills', 'enabled')) == true
    end
    core:remove_listener('wh3pt_auto_skills_turn')
    core:add_listener('wh3pt_auto_skills_turn', 'FactionTurnStart', true, function(c)
        env.guard(function()
            if enabled and c:faction():name() == env.faction():name() then api.soon(0.5) end
        end)
    end, true)
    core:remove_listener('wh3pt_auto_skills_rank')
    core:add_listener('wh3pt_auto_skills_rank', 'CharacterRankUp', true, function(c)
        env.guard(function()
            if enabled and c:character():faction():name() == env.faction():name() then api.soon(0.3) end
        end)
    end, true)
    api.reset()
    return api
end
