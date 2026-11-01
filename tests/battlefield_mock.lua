-- A small battlefield for the commander: units stand where a test puts them, orders are
-- recorded, and march() moves them. It checks decisions; it is not a combat simulator.
clock, is_conflict, in_cutscene, attacker_side = 0, true, false, false
orders, order_log, planner_units, planner_calls = {}, {}, {}, 0
selected_ids = {}
ready_text, context_broken = nil, false
-- How the engine reports script orders: not at all, inside the call, or a tick later.
echo_mode, echo_handler, late_echoes_pending = nil, nil, 0
game_speed, planner_refuses, control_log = 1, false, {}

function vec(x, z)
    return {x=x, z=z, get_x=function(s) return s.x end, get_y=function() return 0 end, get_z=function(s) return s.z end}
end
function v(x, y, z) return vec(x, z) end
function v_to_ground(p) return p end

function collection(items)
    return {count=function() return #items end, item=function(_, i) return items[i] end}
end

local function record(u, kind, fields)
    fields = fields or {}
    fields.kind, fields.at = kind, clock
    order_log[#order_log + 1] = {unit=u.id, order=fields}
    orders[u.id] = fields
    u.idle = kind == 'halt'
    u.script_held = true
    if echo_mode == 'sync' then echo_handler() elseif echo_mode == 'late' then late_echoes_pending = late_echoes_pending + 1 end
end

local next_id = 100
function make_unit(side, key, x, z, extra)
    next_id = next_id + 1
    local u = {id=next_id, side=side, key=key, x=x, z=z, facing=side == 'enemy' and 180 or 0, men=100, hp=1,
        initial=100, ammo=0, start_ammo=0, reach=0, speed=5, walk=3, flying=false, flies=false, routing=false,
        fighting=false, idle=true, visible=true, valid=true, deployed=true, controllable=true, rampaging=false,
        leaving=false, shot_at=false, wavering=false, class='inf_mel', attributes={}, behaviours={}, abilities={},
        lord=false, player=side == 'player', width=40, value=500, script_held=false, locked=false}
    for k, value in pairs(extra or {}) do u[k] = value end
    function u:unique_ui_id() return self.id end
    function u:type() return self.key end
    function u:unit_class() return self.class end
    function u:position() return vec(self.x, self.z) end
    function u:bearing() return self.facing end
    function u:number_of_men_alive() return self.men end
    function u:initial_number_of_men() return self.initial end
    function u:unary_hitpoints() return self.hp end
    function u:is_routing() return self.routing end
    function u:is_in_melee() return self.fighting end
    function u:is_currently_flying() return self.flying end
    function u:can_fly() return self.flies end
    function u:is_under_missile_attack() return self.shot_at end
    function u:is_deployed() return self.deployed end
    function u:is_leaving_battle() return self.leaving end
    function u:is_valid_target() return self.valid end
    function u:is_visible_to_alliance() return self.visible end
    function u:is_player_controlled() return self.player end
    function u:is_controllable() return self.controllable end
    function u:is_rampaging() return self.rampaging end
    function u:is_idle() return self.idle end
    function u:is_wavering() return self.wavering end
    function u:is_commanding_unit() return self.lord end
    function u:is_artillery() return self.class == 'art_fld' or self.class == 'art_fix' end
    function u:is_fixed_artillery() return self.class == 'art_fix' end
    function u:is_cavalry() return self.class == 'cav_shk' or self.class == 'cav_mel' or self.class == 'cav_mis' end
    function u:is_chariot() return self.class == 'chariot' end
    function u:is_war_machine() return false end
    function u:is_war_beasts() return false end
    function u:is_anti_cavalry_infantry() return self.class == 'inf_spr' end
    function u:is_pikemen() return false end
    function u:can_use_magic() return self.magic == true end
    function u:strategic_value() return self.value end
    function u:fast_speed() return self.speed end
    function u:slow_speed() return self.walk end
    function u:missile_range() return self.reach end
    function u:starting_ammo() return self.start_ammo end
    function u:ammo_left() return self.ammo end
    function u:has_attribute(name) return self.attributes[name] == true end
    function u:can_use_behaviour(name) return self.behaviours[name] ~= nil end
    function u:is_behaviour_active(name) return self.behaviours[name] == true end
    function u:ordered_width() return self.width end
    function u:can_reach_position() return self.no_path ~= true end
    function u:owned_non_passive_special_abilities() return self.abilities end
    function u:unit_in_range(target)
        local dx, dz = self.x - target.x, self.z - target.z
        return math.sqrt(dx * dx + dz * dz) <= self.reach
    end
    function u:army() return self.owner end
    return u
end

local function controller_for()
    local uc = {}
    function uc:add_units(u)
        if u.locked then error('unit is in a locked group') end
        self.unit = u
    end
    function uc:release_control()
        self.unit.script_held = false
        self.unit.released = (self.unit.released or 0) + 1
        control_log[#control_log + 1] = 'release ' .. self.unit.id
    end
    function uc:attack_unit(target, primary, run)
        assert(target.side ~= self.unit.side, 'attacked a friend')
        assert(target.men > 0, 'attacked a dead unit')
        record(self.unit, 'attack', {target=target, run=run, melee_mode=self.unit.melee_mode})
    end
    function uc:goto_location(p, run) record(self.unit, 'move', {x=p.x, z=p.z, run=run}) end
    function uc:goto_location_angle_width(p, facing, width, run)
        assert(width > 0, 'width must be positive')
        record(self.unit, 'move', {x=p.x, z=p.z, run=run, facing=facing, width=width})
    end
    function uc:halt() record(self.unit, 'halt') end
    function uc:melee(on) self.unit.melee_mode = on end
    function uc:fire_at_will(on) self.unit.behaviours.fire_at_will = on; self.unit.fire_changes = (self.unit.fire_changes or 0) + 1 end
    function uc:change_behaviour_active(name, on) self.unit.behaviours[name] = on end
    function uc:perform_special_ability(key, target)
        local cast = {key=key, target=target, caster=self.unit, at=clock}
        casts[#casts + 1] = cast
    end
    function uc:perform_special_ability_ground(key, p, bearing, width)
        casts[#casts + 1] = {key=key, ground=p, caster=self.unit, at=clock, bearing=bearing}
    end
    return uc
end
casts = {}

function make_army(player, items, extra)
    local a = {player=player, items=items, current=50, reinforcing=false}
    for k, value in pairs(extra or {}) do a[k] = value end
    function a:units() return collection(self.items) end
    function a:is_player_controlled() return self.player end
    function a:create_unit_controller() return controller_for() end
    function a:winds_of_magic_current() return self.current end
    for _, u in ipairs(items) do u.owner = a end
    return a
end

player_armies, enemy_armies = {}, {}
function reset_field()
    player_armies, enemy_armies = {make_army(true, {})}, {make_army(false, {})}
    orders, order_log, casts, selected_ids = {}, {}, {}, {}
end
function add_player(key, x, z, extra)
    local u = make_unit('player', key, x, z, extra)
    u.owner = player_armies[1]
    table.insert(player_armies[1].items, u)
    return u
end
function add_ally(key, x, z, extra)
    if not player_armies[2] then player_armies[2] = make_army(false, {}) end
    local u = make_unit('player', key, x, z, extra or {})
    u.player = false
    u.owner = player_armies[2]
    table.insert(player_armies[2].items, u)
    return u
end
function add_enemy(key, x, z, extra)
    local u = make_unit('enemy', key, x, z, extra)
    u.owner = enemy_armies[1]
    table.insert(enemy_armies[1].items, u)
    return u
end
reset_field()

local planner = {}
function planner:add_units(u)
    if planner_refuses then error('unit is in a locked group') end
    planner_calls = planner_calls + 1
    planner_units[u.id] = u
end
function planner:remove_units(u)
    planner_units[u.id] = nil
    control_log[#control_log + 1] = 'remove ' .. u.id
end

bm = {}
function bm:time_elapsed_ms() return clock end
function bm:is_conflict_phase() return is_conflict end
function bm:is_any_cutscene_running() return in_cutscene end
function bm:get_player_alliance()
    return {armies=function() return collection(player_armies) end,
        is_attacker=function() return attacker_side end,
        create_ai_unit_planner=function() return planner end}
end
function bm:get_non_player_alliance()
    return {armies=function() return collection(enemy_armies) end}
end

common = {}
function common.get_context_value(kind, id, expression)
    if context_broken then error('no such context') end
    if expression == 'IsSelected' then return selected_ids[tonumber(id)] == true end
    if expression == 'TimeControlContext.CurrentSpeed' then return game_speed end
    return ready_text
end

-- Everyone walks their current order for a second. Melee contact starts at 3m.
local function step_towards(u, x, z, speed)
    local dx, dz = x - u.x, z - u.z
    local distance = math.sqrt(dx * dx + dz * dz)
    if distance <= speed + .5 then
        if distance > .5 then
            u.x, u.z = x - dx / distance * .5, z - dz / distance * .5
        end
        return true
    end
    u.x, u.z = u.x + dx / distance * speed, u.z + dz / distance * speed
end
function march(seconds)
    for _, list in ipairs({player_armies, enemy_armies}) do
        for _, army in ipairs(list) do
            for _, u in ipairs(army.items) do
                local o = orders[u.id]
                if o and o.kind == 'move' and not u.fighting then
                    if step_towards(u, o.x, o.z, (o.run and u.speed or u.walk) * seconds) then
                        u.idle = true
                        orders[u.id] = nil
                    end
                elseif o and o.kind == 'attack' and not u.fighting and not (u.reach > 0 and u:unit_in_range(o.target) and o.melee_mode ~= true) then
                    step_towards(u, o.target.x, o.target.z, (o.run and u.speed or u.walk) * seconds)
                    local dx, dz = o.target.x - u.x, o.target.z - u.z
                    if math.sqrt(dx * dx + dz * dz) <= 3 then
                        u.fighting, o.target.fighting = true, true
                    end
                end
            end
        end
    end
end
