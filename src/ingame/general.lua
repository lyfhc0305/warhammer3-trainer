-- Battle commander. Once a second it reads the field and gives every managed unit one
-- clear job. Control goes straight back after each order, so units stay on the player's
-- cards and an order from the player simply wins for a while. Walled sieges and the
-- 原生 doctrine hand the units to the game's own AI planner instead.
local GENERAL_TUNE = {
    tick = 1000,            -- ms between looks at the field
    order_gap = 2500,       -- a unit is not re-ordered sooner than this unless it is idle
    orders_per_tick = 14,   -- spreads the orders of a huge battle over a few ticks
    casts_per_tick = 2,
    caster_gap = 3000,      -- ms between two casts of one unit
    mark_time = 12000,      -- a target just buffed or hexed is left alone this long
    winds_window = 12000,   -- what was spent this long ago no longer counts against the budget
    winds_floor = 12,       -- winds that may always be spent per window
    winds_share = .4,       -- or this share of what the army has, if more
    winds_spare = 20,       -- single target spells go on ordinary units above this
    winds_overcast = 40,    -- overcasts are preferred from here
    player_pause = 15000,   -- an order from the player holds this long
    hold_limit = 150000,    -- holding ends when nothing has come closer for this long
    march_seconds = 15,     -- one marching step, so the line stays together
    contact = 75,           -- lines this close stop marching and pick targets
    short_step = 8,         -- a march step shorter than this is not worth an order
    charge = 70,            -- run from here
    engage = 45,            -- holding units fight what comes this close
    front_band = 20,        -- how deep behind its front the enemy line counts as the line
    chase_reach = 40,       -- faster enemies further off than this are not worth chasing on foot
    lag_limit = 150,        -- units this far behind the front run to catch up
    safe_gap = 22,          -- closer to a friend than this and a shot risks hitting them
    clear_line = 14,        -- flat shots need this much room on either side of their line
    flank_shot = 70,        -- degrees off the line a shooter goes to fire past a melee
    keep_target = 1.6,      -- a new target must beat the old one by this much
    target_hold = 8000,     -- and the old one gets this long
    rest_hp = .3,           -- leaders pull out at this strength
    recover_hp = .45,       -- and return at this one
    leader_edge = .7,       -- leaders only pick fights at this edge or better
    caster_edge = 1.6,      -- casters that can fight only take on what they clearly beat
    flank_reach = 45,       -- metres to the side of a target when going around it
    charge_hold = 9000,     -- shock cavalry rides out after this long in a melee
    charge_gap = 20000,     -- and not again sooner than this
    breakoff = 55,          -- how far it rides out
    brace_range = 80,       -- spears stand when a charge comes from this close
    brace_hold = 12000,     -- and give up on a charge that has not arrived after this
    reserve_size = 8,       -- armies this big keep a reserve
    reserve_time = 60000,   -- which goes in after this long at the latest
    fail_limit = 3,         -- ticks in a row that may fail before the commander stands down
}
-- What the ability bar of a unit can fire right now, as one comma separated string.
local GENERAL_READY = 'BattleAbilityList.Filter(CanCurrentlyPerform).JoinString(SetupAbilityContext.RecordKey, ",")'

local function general_bits(flags, value)
    return flags % (value * 2) >= value
end

-- Static facts about one unit. The game's tables say what it is for and what it can do;
-- units a mod added after the build still work, from what the engine reports about them.
local function general_profile(u, data)
    local row = data.units and data.units[u:type()]
    local names = data.flags or {}
    local p = {key=u:type()}
    if row then
        for name, bit in pairs(names) do p[name] = general_bits(row[1], bit) end
        p.value, p.armour, p.vs_large, p.vs_inf, p.block = row[2], row[3], row[4], row[5], row[6]
        p.attack, p.defence, p.charge, p.hp = row[7], row[8], row[9], row[10]
        p.damage, p.ap_damage, p.interval, p.splash, p.morale = row[11], row[12], row[13], row[14], row[15]
    else
        local class = u:unit_class()
        p.ranged = class == 'inf_mis' or class == 'cav_mis'
        p.flanker = class == 'cav_mis' or class == 'cav_shk' or class == 'cav_mel' or class == 'chariot'
        p.shock = class == 'cav_shk'
        p.anti_large = u:is_anti_cavalry_infantry() or u:is_pikemen()
        p.brace = p.anti_large
        p.mage = u:can_use_magic()
        p.large = u:is_cavalry() or u:is_chariot() or u:is_war_machine() or u:is_war_beasts()
        p.arty = u:is_artillery()
        p.armour, p.vs_large, p.vs_inf, p.block = 40, 0, 0, 0
    end
    if not p.value or p.value <= 0 then p.value = math.max(200, u:strategic_value() or 0) end
    p.lord = u:is_commanding_unit()
    p.initial = math.max(1, u:initial_number_of_men())
    p.single = p.initial == 1
    p.attack, p.defence, p.charge = p.attack or 30, p.defence or 30, p.charge or 15
    p.hp = p.hp and p.hp > 0 and p.hp or (p.single and 3000 or 70)
    p.damage, p.ap_damage = p.damage or 20, p.ap_damage or 8
    p.interval = p.interval and p.interval > 0 and p.interval or 4.5
    p.splash = p.splash or (p.single and 4 or 1)
    p.speed, p.walk = u:fast_speed(), u:slow_speed()
    p.reach = u:missile_range() or 0
    p.fly = u:can_fly()
    p.always_fly = u:has_attribute('always_flying')
    p.no_melee = u:has_attribute('melee_disabled')
    p.no_shoot = u:has_attribute('shoot_disabled')
    p.spear = p.anti_large
    p.has_ammo = u:starting_ammo() ~= 0 and p.reach > 0 and not p.no_shoot
    p.fixed = u:is_fixed_artillery()
    p.skirmish = u:can_use_behaviour('skirmish')
    p.fire_at_will = u:can_use_behaviour('fire_at_will')
    -- Anything with ammunition that CA does not call a melee unit is a shooter.
    p.shooter = p.has_ammo and not p.arty and (p.ranged or not p.melee)
    return p
end

local function general_dist(a, b)
    local dx, dz = a.x - b.x, a.z - b.z
    return math.sqrt(dx * dx + dz * dz)
end

local function general_bearing(dx, dz)
    return math.deg(math.atan2(dx, dz)) % 360
end

-- 1 from behind, 0 head on, seen from the target's own facing.
local function general_rear(attacker, target)
    local dx, dz = attacker.x - target.x, attacker.z - target.z
    local length = math.sqrt(dx * dx + dz * dz)
    if length < 1 then return .5 end
    local angle = math.rad(target.facing)
    local front = (dx * math.sin(angle) + dz * math.cos(angle)) / length
    return (1 - front) / 2
end

-- Damage per second from the game's own numbers: the chance to hit, what gets through the
-- armour, how often, and how many men of a block can reach the enemy at once.
local function general_blows(a, e)
    local s, t = a.s, e.s
    local bonus = t.large and s.vs_large or s.vs_inf
    local hit = math.max(8, math.min(90, 35 + s.attack + bonus - t.defence)) / 100
    local stopped = math.min(1, t.armour / 100) * .75
    local blow = (s.damage + bonus) * (1 - stopped) + s.ap_damage
    local men = s.single and 1 or math.max(1, a.men) ^ .75
    return hit * blow * (t.large and 1 or s.splash) / s.interval * men
end

local function general_toughness(rec)
    return rec.s.hp * rec.s.initial * math.max(.05, rec.strength)
end

-- Above 1 this unit wins the exchange, below 1 it loses it.
local function general_edge(a, e)
    local needed = general_toughness(e) / math.max(1, general_blows(a, e))
    local left = general_toughness(a) / math.max(1, general_blows(e, a))
    return math.max(.33, math.min(3, left / needed))
end

local function general_can_melee(a, e)
    local s = a.s
    if s.no_melee then return false end
    if e.flying then return s.fly end
    return not s.always_fly
end

-- Reasons to go for one enemy rather than another, whoever does the fighting.
local function general_melee_score(a, e, load, field)
    local distance = general_dist(a, e)
    -- Whether the fight can be won comes first, worth only tips the balance. Rooted, so
    -- that an easy target does not pull a whole line away from what stands in front of it.
    local score = general_edge(a, e) ^ .6 * e.worth ^ .25
    -- A horse does not charge a spear wall, unless the spears are busy with someone else.
    if a.s.large and e.s.spear and not a.s.single then score = score * (e.fighting and .8 or .35) end
    if e.routing then score = score * .15 end
    if e.fighting then
        -- Help whoever is already fighting it, and hit it from where it is not looking.
        score = score * (1.25 + general_rear(a, e) * .9)
    end
    if e.threatens_support then score = score * 1.7 end
    if e.flanking then score = score * 1.5 end
    if e.wavering then score = score * 1.4 end
    -- Finish what is nearly broken, it frees a whole unit.
    score = score * (1.4 - e.strength * .4)
    score = score / (1 + (load[e.id] or 0) * .55)
    -- Straight ahead is closer than sideways: units crossing paths leave gaps in the line.
    local aside = math.abs((e.x - a.x) * field.az - (e.z - a.z) * field.ax)
    return score / (1 + (distance + aside * .6) / (math.max(1, a.s.speed) * 12)), distance
end

-- Cavalry and flyers go for what infantry cannot reach in time.
local function general_flank_score(a, e, load, field)
    local score, distance = general_melee_score(a, e, load, field)
    if e.s.arty then score = score * 2.2 end
    if e.s.shooter and not e.fighting then score = score * 1.8 end
    if e.s.mage and e.s.single then score = score * 1.5 end
    if e.fighting then score = score * (1 + general_rear(a, e)) end
    if e.routing then score = score * .5 end
    return score, distance
end

local function general_shot_score(a, e, load, field)
    local s, t = a.s, e.s
    local distance = general_dist(a, e)
    local score = math.sqrt(e.worth)
    -- Shields only count from the front, and not against shells.
    if not s.arty then score = score * (1 - t.block / 100 * (1 - general_rear(a, e))) end
    score = score * (1 - math.min(t.armour, 150) / (s.arty and 500 or 260))
    if t.shooter or t.arty then score = score * 1.6 end
    if t.mage and t.single then score = score * 1.4 end
    if e.routing then score = score * .2 end
    if e.wavering then score = score * 1.3 end
    if s.arty and s.direct then
        -- A cannon ball goes through one thing: the bigger the better.
        if t.large then score = score * (t.single and 1.8 or 1.4)
        else score = score * (.5 + .5 * math.min(e.men, 100) / 100) end
    elseif s.arty then
        -- Shells are wasted on anything small or fast.
        if t.single then score = score * .5 end
        if t.large and not t.arty then score = score * .8 end
        score = score * (.6 + .4 * math.min(e.men, 100) / 100)
    end
    -- A free target beats one tied up with friends, whatever the angle.
    if e.near < GENERAL_TUNE.safe_gap and not field.free_fire then score = score * .6 end
    -- A few volleys finish a battered unit, a whole line of archers should not need to.
    score = score * (1.5 - e.strength * .5)
    score = score / (1 + (load[e.id] or 0) * .45)
    return score / (1 + distance / 400), distance
end

-- Is there a friend on the line of fire?
local function general_line_blocked(a, e, friends)
    local dx, dz = e.x - a.x, e.z - a.z
    local length = math.max(1, math.sqrt(dx * dx + dz * dz))
    for _, f in ipairs(friends) do
        if f ~= a and not f.flying then
            local fx, fz = f.x - a.x, f.z - a.z
            local along = (fx * dx + fz * dz) / length
            local aside = math.abs(fx * dz - fz * dx) / length
            if along > 0 and along < length and aside < GENERAL_TUNE.clear_line then
                -- Right by the target only the angle matters: from 60 degrees off the
                -- shot passes along the fight instead of through it.
                local short = length - along
                if short > 12 or short / math.max(1, math.sqrt(short * short + aside * aside)) > .5 then return true end
            end
        end
    end
    return false
end

-- Flat shots hit whoever stands in the way, so they need an open line. Arcing shots from
-- behind or beside our own men come down on the enemy. Shells are never dropped next to
-- friends, and large single targets are hit rather than the men around them.
local function general_safe_shot(a, e, friends)
    if a.s.direct and not a.flying and not a.s.arty and general_line_blocked(a, e, friends) then return false end
    if e.near >= GENERAL_TUNE.safe_gap or (e.s.single and e.s.large) then return true end
    return not a.s.arty
end

local function new_general(manager, env)
    local T = GENERAL_TUNE
    local data = env.data or {}
    local api = {}
    local mode, doctrine = 'off', 'auto'
    local phase, phase_at, finished, pursuing = nil, 0, false, false
    local statics, plans, manual, paused, controllers, adopted = {}, {}, {}, {}, {}, {}
    local locked, resting, ours, starters, cast_at, marked, ready_at, used = {}, {}, {}, {}, {}, {}, {}, {}
    local planner, planned, issuing, issuing_at, late_echoes = nil, {}, false, -1, false
    local failures, status, counts = 0, '未托管', {orders=0, casts=0}
    local attacker, begun, march_at, hold_gap = false, false, -60000, 9999
    local fight_since, broke_at, braced, spending, reserves = {}, {}, {}, {}, nil
    local function now() return manager:time_elapsed_ms() end
    local function log(text) if env.log then env.log(text) end end
    local function alliance() return manager:get_player_alliance() end

    local function wanted(rec)
        local pick = manual[rec.id]
        if pick ~= nil then return pick end
        if mode == 'all' then return true end
        if mode == 'no_lord' then return not rec.s.lord end
        if mode == 'reinforcements' then return not starters[rec.id] end
        return false
    end
    local function read_unit(u, friend)
        local id = u:unique_ui_id()
        local s = statics[id]
        if not s then s = general_profile(u, data); statics[id] = s end
        local p = u:position()
        local strength = math.min(1, u:unary_hitpoints())
        return {id=id, u=u, s=s, x=p:get_x(), z=p:get_z(), men=u:number_of_men_alive(), strength=strength,
            facing=u:bearing(), worth=s.value * (.35 + .65 * strength), routing=u:is_routing(),
            fighting=u:is_in_melee(), flying=u:is_currently_flying(), shot_at=u:is_under_missile_attack(),
            wavering=u:is_wavering(), shattered=u:is_shattered(), fast=u:is_moving_fast(),
            near=9999, friend=friend}
    end
    -- Everything that is standing, on both sides, read once per tick.
    local function read_side(armies, friend, visible_to, unseen)
        local list = {}
        for a = 1, armies:count() do
            local army = armies:item(a)
            local units = army:units()
            for i = 1, units:count() do
                local u = units:item(i)
                if u:number_of_men_alive() > 0 and u:is_deployed() and not u:is_leaving_battle() then
                    if friend or (u:is_valid_target() and u:is_visible_to_alliance(visible_to)) then
                        local rec = read_unit(u, friend)
                        rec.player = friend and army:is_player_controlled()
                            and (ours[rec.id] or u:is_player_controlled())
                        if rec.player then ours[rec.id] = true end
                        list[#list + 1] = rec
                    elseif unseen then
                        unseen[#unseen + 1] = u:position()
                    end
                end
            end
        end
        return list
    end
    local function read_field()
        local mine = alliance()
        local friends = read_side(mine:armies(), true, mine)
        local unseen = {}
        local enemies = read_side(manager:get_non_player_alliance():armies(), false, mine, unseen)
        local field = {friends=friends, enemies=enemies, managed={}, standing={}, by_id={}, infantry=0,
            fights=0, under_fire=0, our_shots=0, their_shots=0, our_power=0, their_power=0,
            free_fire=env.free_fire and env.free_fire()}
        local cx, cz, count = 0, 0, 0
        for _, rec in ipairs(friends) do
            if not rec.routing then
                cx, cz, count = cx + rec.x, cz + rec.z, count + 1
                field.our_power = field.our_power + rec.worth
                if rec.fighting then field.fights = field.fights + 1 end
                if rec.shot_at then field.under_fire = field.under_fire + 1 end
                if rec.s.shooter or rec.s.arty then
                    field.our_shots = field.our_shots + rec.worth
                elseif not rec.s.flanker and not rec.s.mage then
                    field.infantry = field.infantry + 1
                end
            end
            if rec.player and wanted(rec) then field.managed[#field.managed + 1] = rec end
        end
        -- Standing enemies point the way; failing that the routed, or those out of sight.
        local ex, ez, seen = 0, 0, 0
        for _, e in ipairs(enemies) do
            field.by_id[e.id] = e
            if not e.routing then
                field.standing[#field.standing + 1] = e
                ex, ez, seen = ex + e.x, ez + e.z, seen + 1
                field.their_power = field.their_power + e.worth
                if e.s.shooter or e.s.arty then field.their_shots = field.their_shots + e.worth end
            end
        end
        if seen == 0 then
            for _, e in ipairs(enemies) do ex, ez, seen = ex + e.x, ez + e.z, seen + 1 end
        end
        if seen == 0 then
            for _, p in ipairs(unseen) do ex, ez, seen = ex + p:get_x(), ez + p:get_z(), seen + 1 end
        end
        if count == 0 then return field end
        field.cx, field.cz = cx / count, cz / count
        if seen == 0 then return field end
        field.ex, field.ez = ex / seen, ez / seen
        local ax, az = field.ex - field.cx, field.ez - field.cz
        local length = math.max(1, math.sqrt(ax * ax + az * az))
        field.ax, field.az = ax / length, az / length
        return field
    end
    local function along(field, rec)
        return (rec.x - field.cx) * field.ax + (rec.z - field.cz) * field.az
    end
    -- Who is near whom. Everything else is decided from these numbers.
    local function measure(field)
        local front, enemy_front, any_front = -9999, 9999, 9999
        for _, rec in ipairs(field.friends) do
            if not rec.routing then
                if field.ax and not rec.s.shooter and not rec.s.arty and not rec.s.mage then
                    front = math.max(front, along(field, rec))
                end
                for _, e in ipairs(field.enemies) do
                    if rec.flying == e.flying or rec.fighting then
                        local distance = general_dist(rec, e)
                        if distance < e.near then e.near = distance end
                        if distance < 12 and e.fighting and (rec.s.shooter or rec.s.arty or rec.s.mage or rec.s.lord) then
                            e.threatens_support = true
                        end
                    end
                end
            end
        end
        -- Skirmishers, horsemen and lone heroes out in front are not the enemy line.
        for _, e in ipairs(field.standing) do
            e.along = along(field, e)
            any_front = math.min(any_front, e.along)
            local t = e.s
            if not (t.harasser or t.shooter or t.arty or t.single or t.flanker) then
                enemy_front = math.min(enemy_front, e.along)
            end
        end
        if enemy_front == 9999 then enemy_front = any_front end
        for _, rec in ipairs(field.friends) do
            rec.along = along(field, rec)
        end
        for _, rec in ipairs(field.managed) do
            rec.nearest, rec.nearest_dist, rec.threat, rec.threat_dist = nil, 9999, nil, 9999
            for _, e in ipairs(field.standing) do
                local distance = general_dist(rec, e)
                if distance < rec.nearest_dist then rec.nearest, rec.nearest_dist = e, distance end
                -- Nothing busy with our line, or standing off to shoot, is coming for this unit.
                if distance < rec.threat_dist and not e.fighting and not e.s.shooter and not e.s.arty then
                    rec.threat, rec.threat_dist = e, distance
                end
            end
        end
        field.front, field.enemy_front = front, enemy_front
        field.gap = enemy_front - (front > -9999 and front or 0)
    end
    -- Who is working around our flanks, and who is making for the men who cannot fight back.
    local function note_threats(field)
        local function mark(u)
            local e = u and field.by_id[u:unique_ui_id()]
            if e then e.flanking = true end
        end
        local ids = {}
        for _, rec in ipairs(field.friends) do
            ids[rec.id] = rec
            if not rec.routing then
                local u = rec.u
                if u:is_left_flank_threatened() then mark(u:left_flank_threat()) end
                if u:is_right_flank_threatened() then mark(u:right_flank_threat()) end
                if u:is_rear_flank_threatened() then mark(u:rear_threat()) end
            end
        end
        for _, e in ipairs(field.standing) do
            local target = e.near < 150 and e.u:current_target()
            local rec = target and ids[target:unique_ui_id()]
            if rec and (rec.s.shooter or rec.s.arty or rec.s.mage or rec.s.lord) then e.threatens_support = true end
        end
    end

    -- Orders ------------------------------------------------------------------------
    local function controller(id, u)
        local uc = controllers[id]
        if not uc then
            uc = u:army():create_unit_controller()
            uc:add_units(u)
            controllers[id] = uc
        end
        return uc
    end
    -- Control goes back even if the order fails. A unit in a locked group refuses
    -- controllers altogether, so it is left to the player for a while.
    local function command(rec, fn)
        issuing, issuing_at = true, now()
        local ok, err = pcall(function() fn(controller(rec.id, rec.u)) end)
        local uc = controllers[rec.id]
        if uc then pcall(function() uc:release_control() end) end
        issuing = false
        if not ok then
            locked[rec.id] = now() + 10000
            log('order refused for ' .. tostring(rec.s.key) .. ': ' .. tostring(err))
        end
        return ok
    end
    local function ground(x, z) return v_to_ground(v(x, 0, z)) end
    local function give(uc, plan)
        if plan.kind == 'attack' then
            if plan.ranged ~= nil then uc:melee(not plan.ranged) end
            uc:attack_unit(plan.unit, true, plan.run)
        elseif plan.width then
            uc:goto_location_angle_width(ground(plan.x, plan.z), plan.facing, plan.width, plan.run)
        elseif plan.kind == 'move' then
            uc:goto_location(ground(plan.x, plan.z), plan.run)
        else
            uc:halt()
        end
    end
    local function same_order(a, b)
        if not a or a.kind ~= b.kind or a.run ~= b.run then return false end
        if a.kind == 'attack' then return a.target == b.target and a.ranged == b.ranged end
        if a.kind == 'move' then return math.abs(a.x - b.x) + math.abs(a.z - b.z) < 10 end
        return true
    end
    local function order(rec, plan, field)
        local old, time = plans[rec.id], now()
        local idle = rec.u:is_idle()
        if same_order(old, plan) and not (idle and time - old.at >= T.order_gap) then return false end
        -- Something urgent may cut in once, not on every tick of a running retreat.
        local cut_in = plan.urgent and not (old and old.urgent)
        if old and time - old.at < T.order_gap and not cut_in and not idle then return false end
        if field.orders >= field.order_limit then return false end
        field.orders = field.orders + 1
        plan.at = time
        plan.since = old and old.target == plan.target and old.since or time
        if command(rec, function(uc) give(uc, plan) end) then
            plans[rec.id] = plan
            counts.orders = counts.orders + 1
            -- The script cannot read melee mode, so it has to remember setting it.
            if plan.ranged == false and adopted[rec.id] then adopted[rec.id].melee_set = true end
            return true
        end
        return false
    end
    local function attack(e, run, ranged)
        return {kind='attack', target=e.id, unit=e.u, run=run, ranged=ranged}
    end
    local function move(x, z, run, facing, width)
        return {kind='move', x=x, z=z, run=run, facing=facing, width=width}
    end

    -- A unit keeps its target until something clearly better comes along.
    local function sticky(rec, field, best, best_score, score_of, load, allowed)
        local old = plans[rec.id]
        local current = old and old.target and field.by_id[old.target]
        if not current or current.routing or not best or not allowed(rec, current, field) then return best end
        if current.id == best.id then return best end
        local kept = score_of(rec, current, load, field)
        if now() - (old.since or 0) < T.target_hold or best_score < kept * T.keep_target then return current end
        return best
    end
    local function pick(rec, field, load, score_of, allowed)
        local best, best_score = nil, 0
        for _, e in ipairs(field.standing) do
            if allowed(rec, e, field) then
                local score = score_of(rec, e, load, field)
                if score > best_score then best, best_score = e, score end
            end
        end
        return sticky(rec, field, best, best_score, score_of, load, allowed)
    end
    local function claim(load, e)
        load[e.id] = (load[e.id] or 0) + 1
        return e
    end
    -- Arrows on a unit are no reason for the infantry to leave it alone, so they count apart.
    local function claims_of(plan, field, load)
        return plan.ranged and field.fire or load
    end
    -- Units that can also shoot have to be told to close in.
    local function melee_order(rec, e, run)
        local plan = attack(e, run)
        if rec.s.has_ammo then plan.ranged = false end
        return plan
    end
    -- Far behind the front a unit runs: the fight will not wait for it.
    local function lagging(rec, field)
        return field.front > -9999 and field.front - rec.along > T.lag_limit
    end
    -- On foot there is no catching what is faster, unless it stands and fights.
    local function catchable(rec, e)
        if not general_can_melee(rec, e) then return false end
        return e.fighting or e.s.speed <= rec.s.speed * 1.2 or general_dist(rec, e) <= T.chase_reach
    end
    -- The line fights the line. What stands behind it is for the cavalry, unless it is
    -- already in reach of something that must be protected.
    local function line_target(rec, e, field)
        if not catchable(rec, e) then return false end
        if e.fighting or e.threatens_support or e.flanking then return true end
        if (e.s.shooter or e.s.arty) and general_dist(rec, e) > T.chase_reach then return false end
        return e.along <= field.enemy_front + T.front_band
    end
    local function plan_melee(rec, field, load, allowed)
        local e = pick(rec, field, load, general_melee_score, allowed or line_target)
        if not e and not allowed then e = pick(rec, field, load, general_melee_score, catchable) end
        if not e then return end
        claim(load, e)
        return melee_order(rec, e, general_dist(rec, e) <= T.charge or lagging(rec, field))
    end
    local function winnable(rec, e, field)
        return line_target(rec, e, field) and general_edge(rec, e) >= T.leader_edge
    end
    -- A leader picks fights it wins and otherwise stays close to its men, where its aura counts.
    local function plan_leader(rec, field, load)
        local plan = plan_melee(rec, field, load, winnable)
        if plan then return plan end
        local nearest, nearest_dist = nil, 9999
        for _, friend in ipairs(field.friends) do
            if friend.fighting and friend ~= rec and general_dist(rec, friend) < nearest_dist then
                nearest, nearest_dist = friend, general_dist(rec, friend)
            end
        end
        if nearest and nearest_dist > 30 then
            return move(nearest.x - field.ax * 20, nearest.z - field.az * 20, true)
        end
    end
    -- From the front a flanker would ride straight into the line, so it goes around
    -- first. Only worth the time when it is not already close.
    local function flank_point(rec, e)
        if general_rear(rec, e) >= .5 or general_dist(rec, e) < 80 then return end
        local angle = math.rad(e.facing)
        local fx, fz = math.sin(angle), math.cos(angle)
        local sx, sz = fz, -fx
        local side = (rec.x - e.x) * sx + (rec.z - e.z) * sz >= 0 and 1 or -1
        local x = e.x + sx * side * T.flank_reach - fx * 15
        local z = e.z + sz * side * T.flank_reach - fz * 15
        if rec.u:can_reach_position(ground(x, z)) then return x, z end
    end
    local function plan_flanker(rec, field, load)
        local allowed = function(a, e)
            if not general_can_melee(a, e) then return false end
            if a.s.single and a.s.high and general_edge(a, e) < T.leader_edge then return false end
            -- Before the lines meet only what stands exposed is worth riding out for.
            return field.free_charge or e.s.arty or (e.s.shooter and e.near > 40)
        end
        local e = pick(rec, field, load, general_flank_score, allowed)
        if not e then return end
        claim(load, e)
        -- Still on the way around: a charge from here would meet the front after all.
        local old = plans[rec.id]
        if old and old.kind == 'move' and old.target == e.id and general_dist(rec, old) >= 15
            and not rec.u:is_idle() then return end
        if e.fighting or e.s.shooter or e.s.arty then
            local x, z = flank_point(rec, e)
            if x then
                local plan = move(x, z, true)
                plan.target = e.id
                return plan
            end
        end
        return melee_order(rec, e, general_dist(rec, e) <= T.charge * 2)
    end
    local function in_range(rec, e)
        return general_dist(rec, e) <= rec.s.reach * 1.05 and rec.u:unit_in_range(e.u)
    end
    -- With friendly fire off nothing stands in the way, so nobody holds fire or walks for an angle.
    local function clear_shot(rec, e, field)
        return general_dist(rec, e) <= rec.s.reach * 1.05
            and (field.free_fire or general_safe_shot(rec, e, field.friends))
            and rec.u:unit_in_range(e.u)
    end
    local function hold_fire(rec, value)
        local record = adopted[rec.id]
        if not (rec.s.fire_at_will and record) or (record.holding or false) == value then return end
        -- Not back and forth while a target drifts in and out of a friend's reach.
        if now() - (record.fire_at or -T.order_gap) < T.order_gap then return end
        record.holding, record.fire_at = value, now()
        command(rec, function(uc) uc:fire_at_will(not value and record.fire_at_will ~= false) end)
    end
    -- Is something in range that may not be shot at? Better no shot than a shot in the back.
    local function tied_up(rec, field)
        for _, e in ipairs(field.standing) do
            if in_range(rec, e) then return true end
        end
        return false
    end
    -- An attack order keeps shooting whatever fire at will says, so it has to be called off.
    local function cease_fire(rec)
        local old = plans[rec.id]
        if old and old.kind == 'attack' and old.ranged then return {kind='halt', urgent=true} end
    end
    local function plan_artillery(rec, field)
        if rec.u:ammo_left() == 0 then return end
        local e = pick(rec, field, field.fire, general_shot_score, clear_shot)
        local risky = not e and tied_up(rec, field)
        hold_fire(rec, risky)
        if e then claim(field.fire, e); return attack(e, false, true) end
        if risky then return cease_fire(rec) end
    end
    -- Keep within reach of the fight, but behind the men doing it.
    local function close_in(rec, field, reach)
        local e, old = rec.nearest, plans[rec.id]
        if not e or rec.nearest_dist <= reach or rec.s.fixed then return end
        if old and old.kind == 'move' and not rec.u:is_idle() then return old end
        local room = rec.nearest_dist - reach + 5
        local ux, uz = (e.x - rec.x) / rec.nearest_dist, (e.z - rec.z) / rec.nearest_dist
        local x, z = rec.x + ux * room, rec.z + uz * room
        if field.front > -9999 then
            local ahead = (x - field.cx) * field.ax + (z - field.cz) * field.az - (field.front - 8)
            if ahead > 0 then x, z = x - field.ax * ahead, z - field.az * ahead end
        end
        -- Well behind the men it runs: the best moment for a spell is before they meet.
        if math.abs(x - rec.x) + math.abs(z - rec.z) >= 12 then
            return move(x, z, field.front > -9999 and field.front - rec.along > 60)
        end
    end
    -- A blocked line of fire opens from the side: walk to the end of the line and shoot
    -- along the fight. Not into the arms of anything that is still free.
    local function find_angle(rec, field)
        local old, e = plans[rec.id], rec.nearest
        if rec.s.fixed or not e then return end
        if old and old.kind == 'move' and not rec.u:is_idle() then return old end
        local sx, sz = field.az, -field.ax
        local side = (rec.x - e.x) * sx + (rec.z - e.z) * sz >= 0 and 1 or -1
        local reach, angle = rec.s.reach * .75, math.rad(T.flank_shot)
        local x = e.x + sx * side * reach * math.sin(angle) - field.ax * reach * math.cos(angle)
        local z = e.z + sz * side * reach * math.sin(angle) - field.az * reach * math.cos(angle)
        if rec.threat and general_dist({x=x, z=z}, rec.threat) < 40 then return end
        if rec.u:can_reach_position(ground(x, z)) then return move(x, z, false) end
    end
    -- Backs away from what is about to reach it, unless that is faster anyway.
    local function back_away(rec, field)
        local e = rec.threat
        if not e or rec.fighting or rec.threat_dist >= 35 or e.s.speed > rec.s.speed * 1.25 then return end
        local spread = math.max(1, rec.threat_dist)
        local ux, uz = (rec.x - e.x) / spread, (rec.z - e.z) / spread
        local plan = move(rec.x + ux * 50 - field.ax * 20, rec.z + uz * 50 - field.az * 20, true)
        plan.urgent = true
        return plan
    end
    local function plan_shooter(rec, field, load)
        local s = rec.s
        if rec.u:ammo_left() == 0 then
            -- Out of arrows it fights, but it does not leave the line to do so.
            if s.no_melee or (phase ~= 'battle' and rec.nearest_dist > T.engage) then return end
            return plan_melee(rec, field, load)
        end
        if s.hybrid and rec.threat_dist < 35 and not s.no_melee then return plan_melee(rec, field, load) end
        local flee = not s.hybrid and back_away(rec, field)
        if flee then return flee end
        local best = pick(rec, field, field.fire, general_shot_score, clear_shot)
        local blocked = not best and tied_up(rec, field)
        hold_fire(rec, blocked)
        if best then
            claim(field.fire, best)
            return attack(best, false, true)
        end
        if blocked then return cease_fire(rec) or find_angle(rec, field) end
        if phase == 'battle' then return close_in(rec, field, s.reach * .9) end
    end
    local function soft_target(rec, e, field)
        return line_target(rec, e, field) and general_dist(rec, e) <= T.engage and general_edge(rec, e) >= T.caster_edge
    end
    -- A caster lives by its spells. One that can also fight only takes on what it clearly
    -- beats, and only while it is healthy.
    local function plan_caster(rec, field, load)
        if phase == 'battle' and rec.s.melee and rec.strength > .6 then
            local plan = plan_melee(rec, field, load, soft_target)
            if plan then return plan end
        end
        local flee = back_away(rec, field)
        if flee then return flee end
        if phase ~= 'hold' then return close_in(rec, field, (rec.s.reach_spell or 130) * .85) end
    end
    -- Leaders are worth more alive than in one more exchange of blows.
    local function needs_rest(rec, field)
        local s = rec.s
        if not (s.lord or (s.high and s.single)) or phase == 'pursuit' then return false end
        if resting[rec.id] then
            if rec.strength >= T.recover_hp then resting[rec.id] = nil; return false end
            return true
        end
        if rec.strength <= T.rest_hp and rec.nearest_dist < 40 then
            resting[rec.id] = true
            return true
        end
        return false
    end
    local function plan_rest(rec, field)
        local x, z = field.cx - field.ax * 60, field.cz - field.az * 60
        if math.abs(x - rec.x) + math.abs(z - rec.z) < 30 then return end
        local plan = move(x, z, true)
        plan.urgent = true
        return plan
    end
    -- Keeps the deployed formation: everyone takes the same step towards the enemy.
    local function plan_march(rec, field)
        if lagging(rec, field) then
            if not field.march and not rec.u:is_idle() then return end
            local catch_up = field.front - rec.along - T.contact
            return move(rec.x + field.ax * catch_up, rec.z + field.az * catch_up, true)
        end
        local room = field.gap - T.contact
        if not field.march or room < T.short_step or rec.s.fixed then return end
        local step = math.min(room, math.max(30, math.min(90, field.pace * T.march_seconds)))
        local width = rec.u:ordered_width()
        return move(rec.x + field.ax * step, rec.z + field.az * step, false,
            general_bearing(field.ax, field.az), width and width > 0 and width or nil)
    end
    local function plan_chase(rec, field, load)
        local s = rec.s
        local old = plans[rec.id]
        if s.arty or rec.fighting then return end
        hold_fire(rec, false)
        if old and old.target and field.by_id[old.target] and not rec.u:is_idle() then return end
        if s.has_ammo and rec.u:ammo_left() ~= 0 then
            for _, e in ipairs(field.enemies) do
                if clear_shot(rec, e, field) then return attack(e, false, true) end
            end
        end
        if s.no_melee then return end
        local best, best_score = nil, 0
        for _, e in ipairs(field.enemies) do
            if general_can_melee(rec, e) then
                local distance = general_dist(rec, e)
                if s.speed >= e.s.speed * 1.05 or distance < 30 then
                    -- The shattered are gone anyway, those who can still rally are not.
                    local score = (100 + e.worth) * (e.shattered and .6 or 1) / (1 + (load[e.id] or 0)) / (1 + distance / 60)
                    if score > best_score then best, best_score = e, score end
                end
            end
        end
        if best then claim(load, best); return melee_order(rec, best, true) end
    end
    -- Spears stand and take a charge: running out to meet it throws the bonus away. A charge
    -- that does not arrive is given up on, or the spears would stand there all day.
    local function plan_brace(rec, field)
        if not (rec.s.spear and rec.s.brace) then return end
        for _, e in ipairs(field.standing) do
            if e.s.large and e.fast and not e.fighting and not e.flying and general_dist(rec, e) <= T.brace_range
                and general_rear(rec, e) < .2 then
                braced[rec.id] = braced[rec.id] or now()
                if now() - braced[rec.id] > T.brace_hold then return end
                local width = rec.u:ordered_width()
                local plan = move(rec.x, rec.z, false, general_bearing(e.x - rec.x, e.z - rec.z),
                    width and width > 0 and width or nil)
                plan.urgent = true
                return plan
            end
        end
        braced[rec.id] = nil
    end
    local function plan_fight(rec, field, load)
        if rec.s.flanker then return plan_flanker(rec, field, load) end
        if rec.s.single and rec.s.high then return plan_leader(rec, field, load) end
        return plan_melee(rec, field, load)
    end
    local function plan_role(rec, field, load)
        local s = rec.s
        if s.arty then return plan_artillery(rec, field) end
        if s.shooter or s.mage then
            local plan
            if s.shooter then plan = plan_shooter(rec, field, load) else plan = plan_caster(rec, field, load) end
            if plan or phase ~= 'advance' then return plan end
            return plan_march(rec, field)
        end
        local stand = plan_brace(rec, field)
        if stand then return stand end
        if phase == 'advance' then
            -- Marching on is no answer to something about to hit the column.
            if rec.threat_dist <= T.engage then return plan_fight(rec, field, load) end
            return plan_march(rec, field)
        end
        if phase == 'hold' then
            if not rec.nearest or rec.nearest_dist > T.engage then return end
            return rec.s.single and rec.s.high and plan_leader(rec, field, load) or plan_melee(rec, field, load)
        end
        return plan_fight(rec, field, load)
    end
    -- Cavalry does its damage in the charge, so it rides out and comes again. Not from a
    -- unit about to break, and not on blown horses.
    local function plan_breakoff(rec, field)
        local s = rec.s
        local e, since = rec.nearest, fight_since[rec.id]
        if not (s.shock and s.flanker) or s.single or not e or not since then return end
        if e.routing or e.wavering or e.strength < .3 then return end
        if now() - since < (e.s.spear and T.charge_hold / 2 or T.charge_hold) then return end
        if now() - (broke_at[rec.id] or -T.charge_gap) < T.charge_gap then return end
        local fatigue = rec.u:fatigue_state()
        if fatigue == 'threshold_very_tired' or fatigue == 'threshold_exhausted' then return end
        local spread = math.max(1, rec.nearest_dist)
        local x, z = rec.x + (rec.x - e.x) / spread * T.breakoff, rec.z + (rec.z - e.z) / spread * T.breakoff
        if not rec.u:can_reach_position(ground(x, z)) then return end
        broke_at[rec.id] = now()
        local plan = move(x, z, true)
        plan.target, plan.urgent = e.id, true
        return plan
    end
    -- Out of the way, close enough to matter.
    local function plan_reserve(rec, field)
        local behind, old = field.front - rec.along, plans[rec.id]
        if field.front > -9999 and behind > 120 then
            if old and old.kind == 'move' and not rec.u:is_idle() then return old end
            local up = behind - 70
            return move(rec.x + field.ax * up, rec.z + field.az * up, false)
        end
    end
    -- Orders from before a change of doctrine would otherwise walk on.
    local function stop_running(rec)
        local old = plans[rec.id]
        if old and old.kind ~= 'halt' and not rec.u:is_idle() then return {kind='halt'} end
    end
    local function plan_for(rec, field, load)
        if phase == 'pursuit' then return plan_chase(rec, field, load) end
        if needs_rest(rec, field) then return plan_rest(rec, field) end
        if rec.fighting then
            if reserves then reserves[rec.id] = nil end
            return plan_breakoff(rec, field)
        end
        if reserves and reserves[rec.id] then return plan_reserve(rec, field) end
        local plan = plan_role(rec, field, load)
        if not plan and phase == 'hold' then return stop_running(rec) end
        return plan
    end
    local function order_rank(rec)
        local s = rec.s
        return s.arty and 1 or s.shooter and 2 or s.flanker and 3 or 4
    end
    local function can_order(rec)
        local time = now()
        if (paused[rec.id] or 0) > time or (locked[rec.id] or 0) > time then return false end
        return rec.u:is_controllable() and not rec.routing and not rec.u:is_rampaging()
    end
    local function remember(rec)
        local record = adopted[rec.id]
        if not record then
            record = {u=rec.u}
            if rec.s.fire_at_will then record.fire_at_will = rec.u:is_behaviour_active('fire_at_will') end
            adopted[rec.id] = record
        end
        return record
    end
    local function adopt(rec)
        local record = remember(rec)
        -- Skirmishers keep their own distance, the game does that better than any script.
        if rec.s.skirmish and rec.s.shooter and not record.skirmish_seen then
            record.skirmish_seen = true
            if not rec.u:is_behaviour_active('skirmish') then
                record.skirmish_set = true
                command(rec, function(uc) uc:change_behaviour_active('skirmish', true, false) end)
            end
        end
    end
    -- A company of horse and one of foot stay out until they are needed.
    local function pick_reserves(field)
        if reserves or phase ~= 'battle' then return end
        reserves = {}
        if #field.managed < T.reserve_size then return end
        local horse, foot
        for _, rec in ipairs(field.managed) do
            local s = rec.s
            if not (rec.fighting or s.shooter or s.arty or s.mage or s.single) then
                if s.flanker then
                    if not horse or s.speed > horse.s.speed then horse = rec end
                elseif not foot or rec.along < foot.along then
                    foot = rec
                end
            end
        end
        if horse then reserves[horse.id] = true end
        if foot then reserves[foot.id] = true end
    end
    local function release_reserves(field)
        if not reserves or not next(reserves) then return end
        local needed = field.fights * 2 >= #field.friends or now() - phase_at > T.reserve_time
        for _, e in ipairs(field.standing) do
            needed = needed or e.flanking or e.threatens_support
        end
        if needed then reserves = {} end
    end
    local function command_units(field)
        field.orders = 0
        local load = {}
        field.fire, field.pace = {}, 99
        pick_reserves(field)
        release_reserves(field)
        for _, rec in ipairs(field.managed) do
            adopt(rec)
            if rec.fighting then fight_since[rec.id] = fight_since[rec.id] or now() else fight_since[rec.id] = nil end
            if not rec.s.arty and rec.s.walk > 0 then field.pace = math.min(field.pace, rec.s.walk) end
            local plan = plans[rec.id]
            if plan and plan.target and field.by_id[plan.target] then claim(claims_of(plan, field, load), field.by_id[plan.target]) end
        end
        field.order_limit = field.march and #field.managed or T.orders_per_tick
        field.free_charge = field.fights > 0 or field.infantry == 0 or phase == 'pursuit'
            or now() - phase_at > 20000
        table.sort(field.managed, function(a, b)
            local ra, rb = order_rank(a), order_rank(b)
            if ra ~= rb then return ra < rb end
            return a.id < b.id
        end)
        for _, rec in ipairs(field.managed) do
            if can_order(rec) then
                local old = plans[rec.id]
                if old and old.target and field.by_id[old.target] and not rec.fighting then
                    local claims = claims_of(old, field, load)
                    claims[old.target] = claims[old.target] - 1
                end
                local plan = plan_for(rec, field, load)
                if plan then order(rec, plan, field) end
            end
        end
    end

    -- Abilities ---------------------------------------------------------------------
    local function owned_abilities(rec)
        if rec.s.abilities and now() < (rec.s.abilities_at or 0) then return rec.s.abilities end
        local list = {}
        for _, key in pairs(rec.u:owned_non_passive_special_abilities()) do
            local row = data.abilities and data.abilities[key]
            if row then
                list[#list + 1] = {key=key, use=data.uses[row[1]], mask=row[2], range=row[3], area=row[4], mana=row[5],
                    recharge=row[6], uses=row[7], wind=row[8], time=row[9], danger=row[10],
                    overcast=string.match(key, '_upgraded$') ~= nil}
            end
        end
        rec.s.abilities, rec.s.abilities_at = list, now() + 10000
        -- A caster stands where its shortest attack spell reaches: the long ones reach anyway.
        local shortest, longest = nil, 0
        for _, ability in ipairs(list) do
            longest = math.max(longest, ability.range)
            local attack_spell = ability.use == 'nuke' or ability.use == 'hex' or ability.use == 'snipe'
                or ability.use == 'hex_shooters'
            if attack_spell and ability.range > 0 then shortest = math.min(shortest or ability.range, ability.range) end
        end
        rec.s.reach_spell = (shortest or longest) > 0 and math.max(80, shortest or longest) or nil
        return list
    end
    -- What the ability bar itself says. If that cannot be read, the tables and our own
    -- bookkeeping decide; a cast the engine refuses costs nothing.
    local function readable_ready(rec)
        if not (common and common.get_context_value) then return end
        local ok, text = pcall(common.get_context_value, 'CcoBattleUnit', tostring(rec.id), GENERAL_READY)
        if not ok or type(text) ~= 'string' or text == '' then return end
        local set = {}
        for key in string.gmatch(text, '[^,]+') do set[key] = true end
        return set
    end
    -- Winds run out long before a battle does, so they are spent over time.
    local function affordable(ability, winds)
        if ability.mana > winds then return false end
        if ability.mana <= 0 or (env.free_winds and env.free_winds()) then return true end
        local spent, time = 0, now()
        for i = #spending, 1, -1 do
            if spending[i].at + T.winds_window < time then table.remove(spending, i)
            else spent = spent + spending[i].mana end
        end
        -- One big spell is always allowed: a budget must not rule out the expensive ones.
        return spent == 0 or spent + ability.mana <= math.max(T.winds_floor, winds * T.winds_share)
    end
    local function usable(rec, ability, ready, winds)
        if not affordable(ability, winds) then return false end
        if ready then return ready[ability.key] == true end
        local key = rec.id .. ability.key
        if (ready_at[key] or 0) > now() then return false end
        return ability.uses < 0 or (used[key] or 0) < ability.uses
    end
    local function may_mark(target, use)
        return (marked[target.id .. use] or 0) <= now()
    end
    local function in_cast_range(rec, target, ability)
        return ability.range <= 0 or general_dist(rec, target) <= ability.range * .95
    end
    -- Mask 0 abilities need no target: they act around the caster.
    local function around_caster(rec, list, ability, wants)
        local radius = math.max(25, ability.area)
        for _, other in ipairs(list) do
            if not other.routing and general_dist(rec, other) <= radius and wants(other) then return rec end
        end
    end
    local function friend_target(rec, field, ability, wants)
        if ability.mask == 0 then return around_caster(rec, field.friends, ability, wants) end
        if ability.mask % 2 == 1 and wants(rec) then return rec end
        if ability.mask % 4 < 2 and ability.mask < 8 then return end
        local best, best_worth = nil, 0
        for _, friend in ipairs(field.friends) do
            if friend ~= rec and not friend.routing and friend.worth > best_worth and in_cast_range(rec, friend, ability)
                and wants(friend) and may_mark(friend, ability.use) then
                best, best_worth = friend, friend.worth
            end
        end
        return best
    end
    local function enemy_target(rec, field, ability, wants, score_of)
        if ability.mask == 0 then return around_caster(rec, field.standing, ability, wants) end
        if ability.mask % 8 < 4 and ability.mask < 8 then return end
        local best, best_score = nil, 0
        for _, e in ipairs(field.standing) do
            if in_cast_range(rec, e, ability) and wants(e) and may_mark(e, ability.use) then
                local score = score_of(e)
                if score > best_score then best, best_score = e, score end
            end
        end
        return best
    end
    -- The densest knot of enemies with none of ours inside the blast, or in its way.
    local function cluster_target(rec, field, ability)
        if ability.mask == 0 then return end
        local radius = math.max(18, ability.area)
        -- While there are several units to choose from a blast has to catch more than one.
        local wanted = #field.standing >= 4 and 2 or 1
        local best, best_score = nil, 0
        for _, e in ipairs(field.standing) do
            if e.near > ability.danger and in_cast_range(rec, e, ability) and may_mark(e, ability.use) then
                local score, caught = 0, 0
                for _, other in ipairs(field.standing) do
                    if general_dist(e, other) <= radius + 12 then score, caught = score + other.worth, caught + 1 end
                end
                if caught >= wanted and score > best_score then best, best_score = e, score end
            end
        end
        return best
    end
    -- Abilities a unit uses for itself still have to be aimed the way the tables say.
    local function own_target(rec, ability)
        if ability.mask == 0 or ability.mask % 2 == 1 then return rec end
        local e = rec.nearest
        if (ability.mask % 8 >= 4 or ability.mask >= 8) and e and in_cast_range(rec, e, ability) then return e end
    end
    local function worth_of(e) return e.worth end
    local function fighting(rec) return rec.fighting end
    local choose = {}
    function choose.heal(rec, field, ability)
        return friend_target(rec, field, ability, function(f) return f.strength < .6 end)
    end
    function choose.ammo(rec, field, ability)
        return friend_target(rec, field, ability, function(f)
            return f.s.has_ammo and f.u:starting_ammo() > 0 and f.u:ammo_left() / f.u:starting_ammo() < .3
        end)
    end
    function choose.rally(rec, field, ability)
        return friend_target(rec, field, ability, function(f) return f.wavering end)
    end
    function choose.shooters(rec, field, ability)
        return friend_target(rec, field, ability, function(f)
            return f.s.shooter and not f.fighting and f.nearest_dist and f.nearest_dist < f.s.reach
        end)
    end
    function choose.cover(rec, field, ability)
        return friend_target(rec, field, ability, function(f) return f.shot_at end)
    end
    function choose.self(rec, field, ability)
        if rec.fighting or (rec.nearest_dist or 9999) < 30 then return own_target(rec, ability) end
    end
    function choose.bless(rec, field, ability)
        return friend_target(rec, field, ability, fighting)
    end
    function choose.panic(rec, field, ability)
        if rec.fighting and rec.strength < .5 then return own_target(rec, ability) end
    end
    function choose.hex_shooters(rec, field, ability)
        return enemy_target(rec, field, ability, function(e) return e.s.shooter end, worth_of)
    end
    function choose.hex(rec, field, ability)
        return enemy_target(rec, field, ability, function(e)
            return e.fighting or (e.s.large and e.near < 100)
        end, worth_of)
    end
    function choose.burst(rec, field, ability)
        local radius, count = math.max(15, ability.area), 0
        for _, e in ipairs(field.standing) do
            if general_dist(rec, e) <= radius then count = count + 1 end
        end
        if count >= 2 or (count == 1 and rec.fighting) then return own_target(rec, ability) end
    end
    function choose.snipe(rec, field, ability)
        -- Heroes and monsters first; anything else only when the winds can bear it.
        return enemy_target(rec, field, ability, function(e)
            return e.s.single or e.s.high or ability.mana == 0 or field.winds >= T.winds_spare
        end, function(e) return e.worth * ((e.s.single or e.s.high) and 2 or 1) end)
    end
    function choose.nuke(rec, field, ability)
        return cluster_target(rec, field, ability)
    end
    function choose.always(rec, field, ability)
        if (rec.nearest_dist or 9999) < 200 then return own_target(rec, ability) end
    end
    function choose.summon(rec, field, ability)
        local e = rec.nearest
        if e and (ability.range <= 0 or rec.nearest_dist <= ability.range * .95) then return e end
    end
    local CAST_ORDER = {heal=1, panic=2, rally=3, burst=4, hex=5, bless=6, nuke=7, snipe=8, summon=9,
        hex_shooters=10, shooters=11, cover=12, self=13, ammo=14, always=15}
    local function cast(rec, ability, target)
        local mask = ability.mask
        local on_ground = mask >= 8
        local on_target = mask % 8 >= 4
        if target == rec then on_target = mask % 2 == 1
        elseif target.friend then on_target = mask % 4 >= 2 end
        local x, z = target.x, target.z
        if ability.use == 'summon' and on_ground then
            -- Summons need room, and the enemy's feet have none.
            local spread = math.max(1, general_dist(rec, target))
            x, z = x + (rec.x - x) / spread * 25, z + (rec.z - z) / spread * 25
            on_target = false
        end
        local bearing = general_bearing(target.x - rec.x, target.z - rec.z)
        return command(rec, function(uc)
            if on_target then
                uc:perform_special_ability(ability.key, target.u)
            elseif on_ground then
                uc:perform_special_ability_ground(ability.key, ground(x, z), bearing, 0)
            else
                uc:perform_special_ability(ability.key)
            end
        end)
    end
    local function note_cast(rec, ability, target)
        local time, key = now(), rec.id .. ability.key
        cast_at[rec.id] = time
        ready_at[key] = time + (env.free_cooldowns and env.free_cooldowns() and 0 or ability.recharge * 1000)
        used[key] = (used[key] or 0) + 1
        marked[target.id .. ability.use] = time + math.max(T.mark_time, ability.time * 1000)
        -- Any order during the wind up could cost the spell.
        if ability.wind > 0 then locked[rec.id] = time + ability.wind * 1000 end
        if ability.mana > 0 then spending[#spending + 1] = {at=time, mana=ability.mana} end
        counts.casts = counts.casts + 1
    end
    local function cast_abilities(field)
        if not field.ax then return end
        local casts, time = 0, now()
        for _, rec in ipairs(field.managed) do
            if casts >= T.casts_per_tick then return end
            if (cast_at[rec.id] or -T.caster_gap) + T.caster_gap <= time and can_order(rec) then
                local list = owned_abilities(rec)
                if #list > 0 then
                    local winds = rec.u:army():winds_of_magic_current()
                    local ready = readable_ready(rec)
                    field.winds = winds
                    local candidates = {}
                    for _, ability in ipairs(list) do
                        -- Overcasting only when the winds are plentiful.
                        if usable(rec, ability, ready, winds) and (not ability.overcast or winds >= 30) then
                            candidates[#candidates + 1] = ability
                        end
                    end
                    table.sort(candidates, function(a, b)
                        local ra, rb = CAST_ORDER[a.use] or 20, CAST_ORDER[b.use] or 20
                        if ra ~= rb then return ra < rb end
                        if a.overcast ~= b.overcast and winds >= T.winds_overcast then return a.overcast end
                        return a.key < b.key
                    end)
                    for _, ability in ipairs(candidates) do
                        local target = choose[ability.use] and choose[ability.use](rec, field, ability)
                        if target and cast(rec, ability, target) then
                            note_cast(rec, ability, target)
                            casts = casts + 1
                            break
                        end
                    end
                end
            end
        end
    end

    -- The game's own AI -------------------------------------------------------------
    -- Control first, then the planner: the order CA's own planner lets go of a unit in.
    local function leave_planner(id)
        pcall(function() controller(id, planned[id]):release_control() end)
        pcall(function() planner:remove_units(planned[id]) end)
        planned[id] = nil
    end
    local function release_unit(id)
        local record = adopted[id]
        adopted[id], plans[id], resting[id] = nil, nil, nil
        if planned[id] then leave_planner(id) end
        if not record then return end
        pcall(function()
            local uc = controller(id, record.u)
            if record.holding then uc:fire_at_will(record.fire_at_will ~= false) end
            if record.skirmish_set then uc:change_behaviour_active('skirmish', false, false) end
            if record.melee_set then uc:melee(false) end
            uc:release_control()
        end)
    end
    local function sync_native(field)
        planner = planner or alliance():create_ai_unit_planner()
        for _, rec in ipairs(field.managed) do
            if not planned[rec.id] and can_order(rec) then
                remember(rec)
                if pcall(function() planner:add_units(rec.u) end) then
                    planned[rec.id] = rec.u
                else
                    locked[rec.id] = now() + 10000
                end
            end
        end
    end

    -- The battle as a whole ---------------------------------------------------------
    local function opening(field)
        if doctrine == 'attack' then return 'advance' end
        if doctrine == 'hold' then return 'hold' end
        if attacker then return 'advance' end
        if field.our_shots > field.their_shots * 1.2 then return 'hold' end
        -- Enemies out of sight count for nothing here, so nothing in sight proves nothing.
        if #field.standing > 0 and field.our_power > field.their_power * 1.3 then return 'advance' end
        return 'hold'
    end
    local function set_phase(name)
        if phase ~= name then
            phase, phase_at, hold_gap = name, now(), 9999
            log('phase: ' .. name)
        end
    end
    local function update_phase(field)
        if pursuing then return set_phase('pursuit') end
        if doctrine == 'hold' then return set_phase('hold') end
        if #field.standing == 0 and #field.enemies > 0 then return set_phase('pursuit') end
        if phase == 'pursuit' then set_phase('battle') end
        if not phase then set_phase(opening(field)) end
        if phase == 'battle' then
            -- Nobody left in sight is not the end of the enemy: go and find the rest.
            if #field.standing > 0 or field.fights > 0 then return end
            set_phase('advance')
        end
        -- Marching stops short of contact by up to one short step, so contact starts there.
        if field.fights > 0 or field.gap < T.contact + T.short_step then return set_phase('battle') end
        if phase == 'hold' then
            -- Holding works as long as the enemy comes on or walks into our shooting.
            local shooting = false
            for _, rec in ipairs(field.managed) do
                local plan = plans[rec.id]
                shooting = shooting or (plan and plan.ranged == true and not rec.u:is_idle())
            end
            if shooting or field.gap < hold_gap - 10 then
                hold_gap, phase_at = math.min(hold_gap, field.gap), now()
            end
            local shelled = field.under_fire >= 2 and field.our_shots <= field.their_shots
            if doctrine == 'attack' or shelled or now() - phase_at >= T.hold_limit then set_phase('advance') end
        end
        if phase == 'advance' and now() - march_at >= T.march_seconds * 1000 then
            march_at, field.march = now(), true
        end
    end
    local PHASE_TEXT = {hold='固守', advance='推进', battle='接战', pursuit='追击'}
    local function describe(field)
        local paused_units, time = 0, now()
        for _, until_ms in pairs(paused) do
            if until_ms > time then paused_units = paused_units + 1 end
        end
        local text = '托管 ' .. #field.managed .. ' 队'
        if planner and next(planned) then return text .. ' · 原生 AI 指挥' end
        text = text .. ' · ' .. (PHASE_TEXT[phase] or '待命')
        if paused_units > 0 then text = text .. ' · 玩家接管 ' .. paused_units .. ' 队' end
        return text
    end
    local function step()
        local field = read_field()
        local seen = {}
        for _, rec in ipairs(field.managed) do seen[rec.id] = true end
        for id in pairs(adopted) do
            if not seen[id] then release_unit(id) end
        end
        if #field.managed == 0 then
            status = mode == 'off' and '未托管' or '托管 0 队'
            return
        end
        -- The planner only fights: chasing the beaten is left to the commander.
        if doctrine == 'native' and not pursuing then
            sync_native(field)
            status = describe(field)
            return
        end
        for id in pairs(planned) do leave_planner(id) end
        if not field.ax then status = describe(field); return end
        measure(field)
        pcall(note_threats, field)
        update_phase(field)
        command_units(field)
        cast_abilities(field)
        status = describe(field)
    end
    function api.tick()
        if finished or not (pursuing or manager:is_conflict_phase()) or manager:is_any_cutscene_running() then return end
        if not begun then api.begin() end
        if mode == 'off' and not next(manual) and not next(adopted) then return end
        local ok, err = pcall(step)
        if ok then failures = 0; return end
        failures = failures + 1
        log('tick failed: ' .. tostring(err))
        if failures >= T.fail_limit then
            mode = 'off'
            manual = {}
            pcall(function() for id in pairs(adopted) do release_unit(id) end end)
            status = 'AI 将军已停止：' .. tostring(err)
            if env.report then env.report(status) end
        end
    end

    -- From the player ---------------------------------------------------------------
    local function each_player_unit(fn)
        local armies = alliance():armies()
        for a = 1, armies:count() do
            local army = armies:item(a)
            if army:is_player_controlled() then
                local units = army:units()
                for i = 1, units:count() do fn(units:item(i)) end
            end
        end
    end
    local function selected()
        local list = {}
        if not (common and common.get_context_value) then return list end
        each_player_unit(function(u)
            local id = u:unique_ui_id()
            local ok, value = pcall(common.get_context_value, 'CcoBattleUnit', tostring(id), 'IsSelected')
            if ok and value == true then list[#list + 1] = id end
        end)
        return list
    end
    local function game_paused()
        local ok, speed = pcall(common.get_context_value, 'CcoBattleRoot', '', 'TimeControlContext.CurrentSpeed')
        return ok and speed == 0
    end
    -- The engine reports script orders here as well, at once or a moment later. An order
    -- with nothing selected cannot be the player's, which shows which of the two it is.
    function api.player_order()
        if finished or issuing then return end
        local list = selected()
        if #list == 0 then late_echoes = true; return end
        if late_echoes and now() - issuing_at <= 200 and not game_paused() then return end
        local time = now()
        for _, id in ipairs(list) do
            if adopted[id] then
                paused[id] = time + T.player_pause
                plans[id] = nil
            end
        end
    end
    -- Whoever stands on the field now is the army; anything later is a reinforcement.
    function api.begin()
        begun = true
        attacker = alliance():is_attacker()
        -- Scripted orders cannot find their way over a wall; the game's siege AI can.
        if doctrine == 'auto' and env.walled and env.walled() then doctrine = 'native' end
        each_player_unit(function(u)
            if u:is_player_controlled() then
                ours[u:unique_ui_id()] = true
                if u:is_deployed() then starters[u:unique_ui_id()] = true end
            end
        end)
    end
    local MODES = {'off', 'all', 'no_lord', 'reinforcements'}
    function api.cycle_mode()
        for i, name in ipairs(MODES) do
            if name == mode then mode = MODES[i % #MODES + 1]; break end
        end
        manual = {}
        status = mode == 'off' and '未托管' or '已下令，下一秒接手'
        return mode
    end
    function api.set_mode(name) mode = name end
    function api.mode() return mode end
    local DOCTRINES = {'auto', 'attack', 'hold', 'native'}
    function api.cycle_doctrine()
        for i, name in ipairs(DOCTRINES) do
            if name == doctrine then doctrine = DOCTRINES[i % #DOCTRINES + 1]; break end
        end
        phase = nil
        return doctrine
    end
    function api.doctrine() return doctrine end
    -- Selected units go to the AI, or come back if any of them is already with it.
    function api.toggle_selected()
        local list = selected()
        if #list == 0 then return nil end
        local take_back = false
        for _, id in ipairs(list) do
            if api.managed(id) then take_back = true end
        end
        for _, id in ipairs(list) do
            manual[id] = not take_back
            paused[id] = nil
        end
        return not take_back, #list
    end
    -- A choice counts from the moment it is made, the units follow on the next tick.
    function api.managed(id)
        if manual[id] ~= nil then return manual[id] end
        return adopted[id] ~= nil
    end
    function api.paused(id) return (paused[id] or 0) > now() end
    function api.owns(u) return ours[u:unique_ui_id()] == true end
    function api.victory() pursuing = true end
    function api.stop()
        finished = true
        for id in pairs(adopted) do pcall(release_unit, id) end
    end
    function api.status() return status end
    function api.counts() return counts end
    function api.phase() return phase end
    return api
end
