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
    player_pause = 15000,   -- an order from the player holds this long
    hold_limit = 45000,     -- without contact, automatic holding ends after this
    march_seconds = 15,     -- one marching step, so the line stays together
    contact = 75,           -- lines this close stop marching and pick targets
    short_step = 8,         -- a march step shorter than this is not worth an order
    charge = 70,            -- run from here
    engage = 60,            -- holding units fight what comes this close
    safe_gap = 22,          -- closer to a friend than this and a shot risks hitting them
    keep_target = 1.4,      -- a new target must beat the old one by this much
    target_hold = 8000,     -- and the old one gets this long
    rest_hp = .3,           -- leaders pull out at this strength
    recover_hp = .45,       -- and return at this one
    flank_reach = 45,       -- metres to the side of a target when going around it
    fail_limit = 3,         -- ticks in a row that may fail before the commander stands down
}
-- What the ability bar of a unit can fire right now, as one comma separated string.
local GENERAL_READY = 'BattleAbilityList.Filter(CanCurrentlyPerform).JoinString(SetupAbilityContext.RecordKey, ",")'

local function general_bits(flags, value)
    return flags % (value * 2) >= value
end

-- Static facts about one unit. The game's tables say what it is for; units a mod added
-- after the build still work, from what the engine reports about them.
local function general_profile(u, data)
    local row = data.units and data.units[u:type()]
    local names = data.flags or {}
    local p = {key=u:type()}
    if row then
        for name, bit in pairs(names) do p[name] = general_bits(row[1], bit) end
        p.value, p.armour, p.ap, p.vs_large, p.vs_inf, p.block = row[2], row[3], row[4], row[5], row[6], row[7]
    else
        local class = u:unit_class()
        p.ranged = class == 'inf_mis' or class == 'cav_mis'
        p.flanker = class == 'cav_mis' or class == 'cav_shk' or class == 'cav_mel' or class == 'chariot'
        p.shock = class == 'cav_shk'
        p.anti_large = u:is_anti_cavalry_infantry() or u:is_pikemen()
        p.mage = u:can_use_magic()
        p.large = u:is_cavalry() or u:is_chariot() or u:is_war_machine() or u:is_war_beasts()
        p.arty = u:is_artillery()
        p.armour, p.ap, p.vs_large, p.vs_inf, p.block = 40, 30, 0, 0, 0
    end
    if not p.value or p.value <= 0 then p.value = math.max(200, u:strategic_value() or 0) end
    p.lord = u:is_commanding_unit()
    p.single = u:initial_number_of_men() == 1
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

-- Would this unit do well against that one in melee? Plain numbers from the tables.
local function general_matchup(a, e)
    local s, t = a.s, e.s
    local score = 1
    if t.large then
        score = score + a.s.vs_large / 25
        if s.anti_large then score = score + .6 end
        if s.single and not s.large then score = score - .1 end
    else
        score = score + s.vs_inf / 25
        if s.large and not s.anti_large then score = score + .15 end
    end
    -- Armour only stops the part of a blow that is not armour piercing.
    score = score * (1 - (1 - s.ap / 100) * math.min(t.armour, 150) / 300)
    -- A horse does not charge a spear wall, unless the spears are busy with someone else.
    if s.large and t.spear and not s.single then
        score = score * (e.fighting and .8 or .35)
    end
    return score
end

local function general_can_melee(a, e)
    local s = a.s
    if s.no_melee then return false end
    if e.flying then return s.fly end
    return not s.always_fly
end

local function general_melee_score(a, e, load)
    local distance = general_dist(a, e)
    -- Rooted, so that worth weighs in without drowning the matchup and the distance.
    local score = math.sqrt(e.worth) * general_matchup(a, e)
    if e.routing then score = score * .15 end
    if e.fighting then
        -- Help whoever is already fighting it, and hit it from where it is not looking.
        score = score * (1.25 + general_rear(a, e) * .9)
    end
    if e.threatens_support then score = score * 1.7 end
    if e.s.arty then score = score * 1.3 end
    if e.s.shooter and e.near > 30 then score = score * 1.25 end
    -- Finish what is nearly broken, it frees a whole unit.
    score = score * (1.4 - e.strength * .4)
    score = score / (1 + (load[e.id] or 0) * .55)
    return score / (1 + distance / (math.max(1, a.s.speed) * 12)), distance
end

-- Cavalry and flyers go for what infantry cannot reach in time.
local function general_flank_score(a, e, load)
    local score, distance = general_melee_score(a, e, load)
    if e.s.arty then score = score * 2.2 end
    if e.s.shooter and not e.fighting then score = score * 1.8 end
    if e.s.mage and e.s.single then score = score * 1.5 end
    if e.fighting then score = score * (1 + general_rear(a, e)) end
    if e.routing then score = score * .5 end
    return score, distance
end

local function general_shot_score(a, e, load)
    local s, t = a.s, e.s
    local distance = general_dist(a, e)
    local score = math.sqrt(e.worth)
    -- Shields only count from the front, and not against shells.
    if not s.arty then score = score * (1 - t.block / 100 * (1 - general_rear(a, e))) end
    score = score * (1 - math.min(t.armour, 150) / (s.arty and 500 or 260))
    if t.shooter or t.arty then score = score * 1.6 end
    if t.mage and t.single then score = score * 1.4 end
    if e.routing then score = score * .2 end
    if s.arty then
        -- Shells are wasted on anything small or fast.
        if t.single then score = score * .5 end
        if t.large and not t.arty then score = score * .8 end
        score = score * (.6 + .4 * math.min(e.men, 100) / 100)
    end
    -- A few volleys finish a battered unit, a whole line of archers should not need to.
    score = score * (1.5 - e.strength * .5)
    score = score / (1 + (load[e.id] or 0) * .45)
    return score / (1 + distance / 400), distance
end

-- A target is safe to shoot when nobody of ours stands in the way of the fall of shot.
-- Large single targets are hit rather than the men around them.
local function general_safe_shot(e, tune)
    return e.near >= tune.safe_gap or (e.s.single and e.s.large)
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
    local attacker, begun, march_at = false, false, -60000
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
            fights=0, under_fire=0, our_shots=0, their_shots=0, our_power=0, their_power=0}
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
        local front, enemy_front = -9999, 9999
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
        for _, e in ipairs(field.standing) do
            if field.ax then enemy_front = math.min(enemy_front, along(field, e)) end
        end
        for _, rec in ipairs(field.managed) do
            rec.nearest, rec.nearest_dist = nil, 9999
            for _, e in ipairs(field.standing) do
                local distance = general_dist(rec, e)
                if distance < rec.nearest_dist then rec.nearest, rec.nearest_dist = e, distance end
            end
        end
        field.front, field.enemy_front = front, enemy_front
        field.gap = enemy_front - (front > -9999 and front or 0)
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
        if not current or current.routing or not best or not allowed(rec, current) then return best end
        if current.id == best.id then return best end
        local kept = score_of(rec, current, load)
        if now() - (old.since or 0) < T.target_hold or best_score < kept * T.keep_target then return current end
        return best
    end
    local function pick(rec, field, load, score_of, allowed)
        local best, best_score, best_distance = nil, 0, 0
        for _, e in ipairs(field.standing) do
            if allowed(rec, e) then
                local score, distance = score_of(rec, e, load)
                if score > best_score then best, best_score, best_distance = e, score, distance end
            end
        end
        return sticky(rec, field, best, best_score, score_of, load, allowed)
    end
    local function claim(load, e)
        load[e.id] = (load[e.id] or 0) + 1
        return e
    end
    -- Units that can also shoot have to be told to close in.
    local function melee_order(rec, e, run)
        local plan = attack(e, run)
        if rec.s.has_ammo then plan.ranged = false end
        return plan
    end
    local function plan_melee(rec, field, load)
        local e = pick(rec, field, load, general_melee_score, general_can_melee)
        if not e then return end
        claim(load, e)
        return melee_order(rec, e, general_dist(rec, e) <= T.charge)
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
    local function clear_shot(rec, e)
        return general_safe_shot(e, T) and in_range(rec, e)
    end
    local function hold_fire(rec, value)
        local record = adopted[rec.id]
        if not (rec.s.fire_at_will and record) or (record.holding or false) == value then return end
        -- Not back and forth while a target drifts in and out of a friend's reach.
        if now() - (record.fire_at or -T.order_gap) < T.order_gap then return end
        record.holding, record.fire_at = value, now()
        command(rec, function(uc) uc:fire_at_will(not value and record.fire_at_will ~= false) end)
    end
    -- Is everything in range tied up with friends? Better no shot than a shot in the back.
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
    local function plan_artillery(rec, field, load)
        if rec.u:ammo_left() == 0 then return end
        local e = pick(rec, field, load, general_shot_score, clear_shot)
        local risky = not e and tied_up(rec, field)
        hold_fire(rec, risky)
        if e then claim(load, e); return attack(e, false, true) end
        if risky then return cease_fire(rec) end
    end
    -- Keep within reach of the fight, but behind the men doing it.
    local function close_in(rec, field, reach)
        local e = rec.nearest
        if not e or rec.nearest_dist <= reach or rec.s.fixed then return end
        local room = rec.nearest_dist - reach + 5
        local ux, uz = (e.x - rec.x) / rec.nearest_dist, (e.z - rec.z) / rec.nearest_dist
        local x, z = rec.x + ux * room, rec.z + uz * room
        if field.front > -9999 then
            local ahead = (x - field.cx) * field.ax + (z - field.cz) * field.az - (field.front - 8)
            if ahead > 0 then x, z = x - field.ax * ahead, z - field.az * ahead end
        end
        if math.abs(x - rec.x) + math.abs(z - rec.z) >= 12 then return move(x, z, false) end
    end
    -- Backs away from what is about to reach it, unless that is faster anyway.
    local function back_away(rec, field)
        local e = rec.nearest
        if not e or rec.fighting or rec.nearest_dist >= 35 or e.s.speed > rec.s.speed * 1.25 then return end
        local spread = math.max(1, rec.nearest_dist)
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
        if s.hybrid and rec.nearest_dist < 35 and not s.no_melee then return plan_melee(rec, field, load) end
        local flee = not s.hybrid and back_away(rec, field)
        if flee then return flee end
        local best = pick(rec, field, load, general_shot_score, clear_shot)
        local risky = not best and tied_up(rec, field)
        hold_fire(rec, risky)
        if best then
            claim(load, best)
            return attack(best, false, true)
        end
        if risky then return cease_fire(rec) end
        if phase == 'battle' then return close_in(rec, field, s.reach * .9) end
    end
    local function plan_caster(rec, field)
        local flee = back_away(rec, field)
        if flee then return flee end
        if phase ~= 'hold' then return close_in(rec, field, (rec.s.reach_spell or 130) * .8) end
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
                if clear_shot(rec, e) then return attack(e, false, true) end
            end
        end
        if s.no_melee then return end
        local best, best_score = nil, 0
        for _, e in ipairs(field.enemies) do
            if general_can_melee(rec, e) then
                local distance = general_dist(rec, e)
                if s.speed >= e.s.speed * 1.05 or distance < 30 then
                    local score = (100 + e.worth) / (1 + (load[e.id] or 0)) / (1 + distance / 60)
                    if score > best_score then best, best_score = e, score end
                end
            end
        end
        if best then claim(load, best); return melee_order(rec, best, true) end
    end
    local function plan_role(rec, field, load)
        local s = rec.s
        if s.arty then return plan_artillery(rec, field, load) end
        if s.shooter or (s.mage and not s.melee) then
            local plan
            if s.shooter then plan = plan_shooter(rec, field, load) else plan = plan_caster(rec, field) end
            if plan or phase ~= 'advance' then return plan end
            return plan_march(rec, field)
        end
        if phase == 'advance' then return plan_march(rec, field) end
        if phase == 'hold' then
            if not rec.nearest or rec.nearest_dist > T.engage then return end
            return plan_melee(rec, field, load)
        end
        if s.flanker then return plan_flanker(rec, field, load) end
        return plan_melee(rec, field, load)
    end
    -- Orders from before a change of doctrine would otherwise walk on.
    local function stop_running(rec)
        local old = plans[rec.id]
        if old and old.kind ~= 'halt' and not rec.u:is_idle() then return {kind='halt'} end
    end
    local function plan_for(rec, field, load)
        if phase == 'pursuit' then return plan_chase(rec, field, load) end
        if needs_rest(rec, field) then return plan_rest(rec, field) end
        if rec.fighting then return end
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
    local function command_units(field)
        field.orders = 0
        local load = {}
        field.pace = 99
        for _, rec in ipairs(field.managed) do
            adopt(rec)
            if not rec.s.arty and rec.s.walk > 0 then field.pace = math.min(field.pace, rec.s.walk) end
            local plan = plans[rec.id]
            if plan and plan.target and field.by_id[plan.target] then
                load[plan.target] = (load[plan.target] or 0) + 1
            end
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
                    load[old.target] = load[old.target] - 1
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
        rec.s.reach_spell = nil
        for _, ability in ipairs(list) do
            if ability.range > (rec.s.reach_spell or 0) then rec.s.reach_spell = ability.range end
        end
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
    local function usable(rec, ability, ready, winds)
        if ability.mana > winds then return false end
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
        local best, best_score = nil, 0
        for _, e in ipairs(field.standing) do
            if e.near > ability.danger and in_cast_range(rec, e, ability) and may_mark(e, ability.use) then
                local score = 0
                for _, other in ipairs(field.standing) do
                    if general_dist(e, other) <= radius + 12 then score = score + other.worth end
                end
                if score > best_score then best, best_score = e, score end
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
        return friend_target(rec, field, ability, function(f) return f.u:is_wavering() end)
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
        return enemy_target(rec, field, ability, function(e) return e.fighting end, worth_of)
    end
    function choose.burst(rec, field, ability)
        local radius, count = math.max(15, ability.area), 0
        for _, e in ipairs(field.standing) do
            if general_dist(rec, e) <= radius then count = count + 1 end
        end
        if count >= 2 or (count == 1 and rec.fighting) then return own_target(rec, ability) end
    end
    function choose.snipe(rec, field, ability)
        return enemy_target(rec, field, ability, function(e) return e.s.single or e.s.high end, worth_of)
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
        if phase ~= name then phase, phase_at = name, now(); log('phase: ' .. name) end
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
