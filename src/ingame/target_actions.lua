-- Campaign army ranks use live unit interfaces; never infer units from card IDs.
local function create_target_actions(env)
    local enabled, pending = false, false
    local function context(component, kind)
        if not component then return end
        local ok, u = pcall(UIComponent, component)
        if not ok or not u then return end
        local function read(node)
            local found, value = pcall(function() return node:GetContextObject(kind) end)
            if found and value then
                local valid, id = pcall(function() return value:Call(kind == 'CcoCampaignUnit' and 'UniqueUiId' or 'Key') end)
                if valid and id and id ~= '' then return value end
            end
        end
        -- A click may land on a portrait/experience overlay. Its unit context is
        -- bound on the card or unit_icon child, not necessarily the clicked node.
        for _ = 1, 12 do
            local value = read(u)
            if value then return value end
            local id_ok, id = pcall(function() return u:Id() end)
            if id_ok and (id == 'list_box' or id == 'units' or id == 'units_panel') then return end
            local icon_ok, icon = pcall(function() return UIComponent(u:Find('unit_icon')) end)
            if icon_ok and icon then
                value = read(icon)
                if value then return value end
            end
            local parent_ok, parent = pcall(function() return UIComponent(u:Parent()) end)
            if not parent_ok or not parent or parent == u then return end
            u = parent
        end
    end
    local function army_units()
        local f = env.faction()
        local ch = env.character and env.character()
        assert(ch and not ch:is_null_interface() and ch:faction():name() == f:name(), '请先选中我方军队')
        assert(not ch:is_wounded() and ch:has_military_force(), '请先选中我方在役军队')
        local force = ch:military_force()
        assert(not force:is_armed_citizenry(), '请选择领主军队，不是城镇驻军')
        local units, targets = force:unit_list(), {}
        for i = 0, units:num_items() - 1 do
            local unit = units:item_at(i)
            -- Both commanders and embedded agents are character cards.
            local character = unit:associated_character()
            if not unit:has_unit_commander() and character:is_null_interface() then
                assert(unit:faction():name() == f:name(), '部队归属已改变，请重新选择军队')
                targets[#targets + 1] = unit
            end
        end
        assert(#targets > 0, '当前军队没有可修改的普通部队')
        return targets, f:name()
    end
    local api = {}
    function api.reset()
        pending = false
        enabled = cm:get_saved_value(env.setting('free_skills', 'enabled')) == true
    end
    function api.toggle(value)
        env.faction()
        enabled = value == nil and not enabled or value == true
        cm:set_saved_value(env.setting('free_skills', 'enabled'), enabled)
        env.message(enabled and '免技能点已开：在原生技能图标上右键学习；仍需满足前置与等级' or '免技能点已关闭')
    end
    function api.enabled() return enabled end
    function api.summary()
        local ok, units = pcall(army_units)
        if not ok then return '请选中我方军队；领主和英雄不参与修改' end
        return '本军队 ' .. #units .. ' 队普通/精英部队；排除领主和英雄'
    end
    function api.rank(target)
        assert(not pending, '请等待上次操作回读')
        assert(type(target) == 'number' and target >= 0 and target <= 9 and target % 1 == 0, '部队等级须为 0～9 的整数')
        local units, owner = army_units()
        local errors = {}
        for _, unit in ipairs(units) do
            local ok, err = pcall(function()
                local before = unit:experience_level()
                if before ~= target then cm:add_experience_to_unit(unit, target - before) end
            end)
            if not ok then errors[#errors + 1] = tostring(err) end
        end
        pending = true
        env.callback(function()
            pending = false
            env.guard(function()
                local passed = 0
                for _, unit in ipairs(units) do
                    local ok, actual = pcall(function()
                        assert(not unit:is_null_interface() and unit:faction():name() == owner, '目标部队已失效')
                        return unit:experience_level()
                    end)
                    if ok and actual == target then passed = passed + 1 end
                end
                assert(passed == #units, '等级设置未全部生效：' .. passed .. '/' .. #units .. ' 队达到 ' .. target .. ' 级' .. (#errors > 0 and ('；' .. errors[1]) or ''))
                env.message('本军队 ' .. passed .. ' 队部队已设为 ' .. target .. ' 级；领主/英雄未修改')
            end)
            env.render()
        end, 0.1)
    end
    function api.skill(component)
        env.faction()
        if not enabled then return end
        local skill = context(component, 'CcoCampaignCharacterSkill')
        if not skill then return end
        assert(not pending, '请等待上次操作回读')
        local char = skill:Call('CharacterContext')
        assert(char:Call('IsPlayerCharacter'), '只能学习当前玩家角色的技能')
        local cqi = char:Call('CQI')
        local ch = cm:get_character_by_cqi(cqi)
        assert(ch and not ch:is_null_interface() and ch:faction():name() == env.faction():name(), '角色不属于当前玩家')
        assert(not skill:Call('IsBackgroundSkill'), '不能修改背景技能')
        local level, points = skill:Call('Level'), char:Call('SkillPointsAvailable')
        assert(level < skill:Call('TotalLevels'), '该技能已满级')
        assert(not char:Call('HasUncommitedSkills'), '请先确认或取消原生界面中尚未提交的技能选择，再右键学习')
        cm:add_skill(ch, skill:Call('Key'), false, true)
        pending = true
        env.callback(function()
            pending = false
            env.guard(function()
                assert(skill:Call('Level') == level + 1, '技能未学会：请检查角色等级、前置及互斥条件')
                assert(char:Call('SkillPointsAvailable') == points, '技能点回读不一致，请检查原生界面')
                env.message('已免费学习技能；剩余技能点 ' .. points)
            end)
            env.render()
        end, 0.1)
    end
    api.reset()
    return api
end
