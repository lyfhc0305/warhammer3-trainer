-- Campaign attributes are preferences until the engine commits to a manual battle.
-- BattleBeingFought.is_autoresolved is the engine decision, not a guessed UI click.
local function create_manual_battle(env)
    local api = {}
    local function log(text)
        local logger = type(ModLog) == 'function' and ModLog or out
        logger('[WH3PT manual] ' .. text)
    end
    function api.read(def)
        env.faction()
        if cm:get_saved_value(env.setting(def.id, 'manual_enabled')) ~= true then return false, nil end
        return true, env.validate(def, cm:get_saved_value(env.setting(def.id, 'value')))
    end
    function api.set(def, value)
        env.faction()
        if not def.battle_setting then
            cm:remove_effect_bundle('wh3pt_' .. def.id, env.faction():name())
            assert(not env.read_model(def), '旧战役加成仍存在：' .. def.label)
        end
        if value ~= nil then cm:set_saved_value(env.setting(def.id, 'value'), value) end
        cm:set_saved_value(env.setting(def.id, 'manual_enabled'), value ~= nil)
    end
    function api.cleanup()
        core:svr_save_bool('wh3pt_magic_pending', false)
        local name = env.faction():name()
        for _, def in ipairs(env.catalog) do
            if def.manual_battle and not def.battle_setting then cm:remove_effect_bundle('wh3pt_' .. def.id, name) end
        end
        for _, def in ipairs(env.catalog) do
            if def.manual_battle and not def.battle_setting then assert(not env.read_model(def), '战后加成未撤除：' .. def.label) end
        end
    end
    function api.migrate()
        for _, def in ipairs(env.catalog) do
            if def.manual_battle and cm:get_saved_value(env.setting(def.id, 'manual_enabled')) == nil then
                local active, value = false, nil
                if not def.battle_setting then active, value = env.read_model(def) end
                if active then cm:set_saved_value(env.setting(def.id, 'value'), env.validate(def, value)) end
                cm:set_saved_value(env.setting(def.id, 'manual_enabled'), active)
            end
        end
        api.cleanup()
    end
    function api.begin(context)
        api.cleanup()
        -- Missing or unknown decision is a failure, never a manual-battle fallback.
        local autoresolved = context:is_autoresolved()
        assert(type(autoresolved) == 'boolean', '无法确认是否手动战斗')
        if autoresolved then log('autoresolve: no combat modifiers'); return end
        local ok, result = pcall(function()
            local f, count = env.faction(), 0
            local settings = {magic=false, cooldown=false, silence=false, general=false, revive=0, vigour=false}
            for _, def in ipairs(env.catalog) do
                if def.manual_battle then
                    local active, value = api.read(def)
                    if def.id == 'revive' and active then settings.revive = value end
                    if def.id == 'vigour' then settings.vigour = active end
                    if def.battle_setting then
                        settings[def.battle_setting] = active
                    elseif active then
                        env.apply_model(f, def, value)
                        local actual_on, actual = env.read_model(def)
                        assert(actual_on and math.abs(actual-value) < .001, '手动战斗加成回读失败：' .. def.label)
                        count = count + 1
                    end
                end
            end
            core:svr_save_string('wh3pt_magic_owner', f:name())
            core:svr_save_bool('wh3pt_magic_enabled', settings.magic)
            core:svr_save_bool('wh3pt_cooldown_enabled', settings.cooldown)
            core:svr_save_bool('wh3pt_silence_enabled', settings.silence)
            core:svr_save_bool('wh3pt_general_enabled', settings.general)
            core:svr_save_bool('wh3pt_vigour_enabled', settings.vigour)
            core:svr_save_string('wh3pt_revive_level', tostring(settings.revive))
            -- Commit last; consumed once by the next battle Lua session.
            core:svr_save_bool('wh3pt_magic_pending', true)
            return count
        end)
        if not ok then api.cleanup(); error(result) end
        log('manual battle: applied ' .. result .. ' modifiers')
    end
    function api.start()
        core:remove_listener('wh3pt_manual_begin')
        core:add_listener('wh3pt_manual_begin', 'BattleBeingFought', true,
            function(c) env.guard(function() api.begin(c) end) end, true)
        for _, event in ipairs({'BattleCompleted', 'BattleConflictFinished', 'PendingBattle'}) do
            local key = 'wh3pt_manual_' .. event
            core:remove_listener(key)
            core:add_listener(key, event, true, function()
                env.guard(function() api.cleanup(); log(event .. ': cleared combat modifiers') end)
            end, true)
        end
    end
    return api
end
