import unittest
from unittest.mock import patch
from lupa.lua51 import LuaRuntime

from general_data import FLAGS, USES, ability_use, role_flags, unit_profiles
from tests import ROOT, SRC


def flags(*names):
    return sum(FLAGS[n] for n in names)


# key -> [flags, value, armour, ap%, vs large, vs infantry, block%]
UNITS = {
    'swords': [flags('melee', 'shield'), 500, 40, 25, 0, 0, 35],
    'spears': [flags('melee', 'anti_large', 'shield'), 450, 40, 20, 16, 0, 30],
    'knights': [flags('melee', 'flanker', 'shock', 'large', 'shield'), 1100, 100, 30, 0, 0, 30],
    'archers': [flags('ranged'), 450, 15, 12, 0, 0, 0],
    'mortar': [flags('ranged', 'arty'), 700, 15, 12, 0, 0, 0],
    'streltsi': [flags('hybrid'), 800, 70, 70, 0, 0, 0],
    'wizard': [flags('mage', 'high'), 700, 20, 30, 0, 0, 0],
    'lord': [flags('melee', 'high'), 1500, 90, 50, 0, 10, 0],
    'dragon': [flags('melee', 'high', 'large'), 2500, 120, 60, 20, 0, 0],
    'plate': [flags('melee'), 900, 120, 25, 0, 0, 0],
}
# key -> [use index, mask, range, area, mana, recharge, uses, wind up, duration, danger]
SELF, FRIEND, ENEMY, GROUND = 1, 2, 4, 8


def ability(use, mask, reach=120, area=0, mana=0, recharge=30, uses=-1, wind=0, danger=32):
    return [USES.index(use) + 1, mask, reach, area, mana, recharge, uses, wind, 20, danger]


ABILITIES = {
    'cure': ability('heal', SELF | FRIEND),
    'smite': ability('nuke', ENEMY | GROUND, 200, 20, 8),
    'smite_upgraded': ability('nuke', ENEMY | GROUND, 200, 20, 20),
    'curse': ability('hex', ENEMY, 150),
    'aura': ability('bless', 0, 0, 40),
    'rage': ability('self', 0, 0, 0),
    'bolt': ability('snipe', ENEMY, 300, 0, 5),
    'raise': ability('summon', GROUND, 150),
    'wall': ability('nuke', GROUND, 100, 30),
    'rally_cry': ability('rally', 0, 0, 50),
    'once': ability('hex', ENEMY, 150, uses=1),
    'drift': ability('nuke', ENEMY | GROUND, 200, 10, danger=80),
    'harmless': ability('nuke', ENEMY | GROUND, 200, 10, danger=0),
    'slow_cast': ability('snipe', ENEMY, 300, wind=5),
    'spin': ability('always', ENEMY | GROUND, 120),
    'lash': ability('self', ENEMY, 100),
    'ground_buff': ability('self', GROUND, 100),
    'own_ward': ability('bless', SELF | GROUND, 100),
}


class GeneralTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute((ROOT / 'tests/battlefield_mock.lua').read_text(encoding='utf-8'))
        self.lua.execute((SRC / 'ingame/general.lua').read_text(encoding='utf-8')
                         + '\nreturn nil\n'.replace('return nil', 'NEW_GENERAL = new_general; TUNE = GENERAL_TUNE'))
        self.g = self.lua.globals()
        self.g.DATA = self.table({'flags': FLAGS, 'uses': list(USES), 'units': UNITS, 'abilities': ABILITIES})
        self.g.env_logs = self.lua.table()
        self.general = self.lua.execute('''
            return NEW_GENERAL(bm, {data=DATA, log=function(t) env_logs[#env_logs+1]=t end,
                report=function(t) reported=t end, free_cooldowns=function() return free_cooldowns end})
        ''')

    def table(self, value):
        if isinstance(value, dict):
            return self.lua.table_from({k: self.table(v) for k, v in value.items()})
        if isinstance(value, (list, tuple)):
            return self.lua.table_from([self.table(v) for v in value])
        return value

    def extras(self, extra):
        if 'class_' in extra:
            extra['class'] = extra.pop('class_')
        return self.table(extra)

    def player(self, key, x, z, **extra):
        return self.g.add_player(key, x, z, self.extras(extra))

    def enemy(self, key, x, z, **extra):
        return self.g.add_enemy(key, x, z, self.extras(extra))

    def shooter(self, key='archers', x=0, z=0, reach=160, **extra):
        return self.player(key, x, z, reach=reach, ammo=30, start_ammo=30, class_='inf_mis', **extra)

    def run_ticks(self, count=1, walk=True, gap=1000):
        for _ in range(count):
            self.g.clock = self.g.clock + gap
            self.general.tick()
            if walk:
                self.g.march(gap / 1000)

    def go(self, doctrine=None):
        self.general.set_mode('all')
        while doctrine and self.general.doctrine() != doctrine:
            self.general.cycle_doctrine()

    def order(self, unit):
        return self.g.orders[unit.id]

    def target(self, unit):
        order = self.order(unit)
        return order and order.target

    def assertTargets(self, unit, target):
        self.assertIsNotNone(self.order(unit), 'no order')
        self.assertEqual(self.order(unit).kind, 'attack')
        self.assertEqual(self.target(unit).id, target.id)

    # Handing over -------------------------------------------------------------------
    def test_nothing_happens_while_switched_off(self):
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 40)
        self.run_ticks(3)
        self.assertEqual(len(self.g.order_log), 0)

    def test_orders_leave_units_with_the_player(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(unit).kind, 'attack')
        self.assertFalse(unit.script_held)

    def test_all_but_lord_leaves_the_commander_alone(self):
        lord = self.player('lord', 0, 0, lord=True)
        line = self.player('swords', 30, 0)
        self.enemy('swords', 0, 50)
        self.general.set_mode('no_lord')
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(lord))
        self.assertIsNotNone(self.order(line))

    def test_reinforcements_mode_only_takes_later_arrivals(self):
        first = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.general.set_mode('reinforcements')
        self.run_ticks(1, walk=False)
        late = self.player('swords', 30, 0)
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(first))
        self.assertIsNotNone(self.order(late))

    def test_reinforcements_are_those_arriving_after_the_start_not_after_the_click(self):
        first = self.player('swords', 0, 0)
        waiting = self.player('swords', 0, -300, deployed=False)
        self.enemy('swords', 0, 50)
        self.run_ticks(1, walk=False)           # the battle starts with the commander off
        late = self.player('swords', 30, 0)
        self.run_ticks(2, walk=False)
        self.general.set_mode('reinforcements')
        waiting.deployed = True
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(first))
        self.assertIsNotNone(self.order(late))
        self.assertIsNotNone(self.order(waiting))

    def test_allied_armies_are_never_ordered(self):
        self.g.add_ally('swords', 30, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(2, walk=False)
        self.assertEqual(len(self.g.order_log), 0)

    def test_deployment_and_cutscenes_give_no_orders(self):
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.g.is_conflict = False
        self.run_ticks(1, walk=False)
        self.g.is_conflict = True
        self.g.in_cutscene = True
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.g.order_log), 0)
        self.g.in_cutscene = False
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.g.order_log), 1)

    def test_units_not_controllable_are_skipped(self):
        for extra in ({'controllable': False}, {'routing': True}, {'rampaging': True}):
            unit = self.player('swords', 0, 0, **extra)
            self.enemy('swords', 0, 50)
            self.go()
            self.run_ticks(1, walk=False)
            self.assertIsNone(self.order(unit), extra)
            self.g.reset_field()

    def test_a_locked_group_is_left_alone_and_the_rest_carry_on(self):
        stuck = self.player('swords', 0, 0, locked=True)
        free = self.player('swords', 30, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(stuck))
        self.assertIsNotNone(self.order(free))
        calls = len(self.g.order_log)
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.g.order_log), calls)

    # The player stays in command ----------------------------------------------------
    def test_a_players_order_wins_for_a_while(self):
        unit = self.player('swords', 0, 0)
        other = self.player('swords', 30, 0)
        foe = self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
        self.g.selected_ids[unit.id] = True
        self.g.clock = self.g.clock + 500
        self.general.player_order()
        before = len(self.g.order_log)
        self.g.orders[unit.id] = None
        unit.idle = True
        other.idle = False
        self.run_ticks(3, walk=False)
        self.assertIsNone(self.order(unit))
        self.g.selected_ids[unit.id] = None
        self.run_ticks(14, walk=False)
        self.assertIsNotNone(self.order(unit))
        self.assertTargets(unit, foe)
        self.assertGreater(len(self.g.order_log), before)

    def test_orders_the_engine_echoes_at_once_are_not_the_players(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.g.echo_handler = self.general.player_order
        self.g.echo_mode = 'sync'
        self.go()
        self.g.selected_ids[unit.id] = True
        self.run_ticks(1, walk=False)
        self.assertFalse(self.general.paused(unit.id))

    def test_orders_the_engine_echoes_later_are_learned_and_not_the_players(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
        self.general.player_order()     # nothing selected: this one cannot be the player's
        self.g.selected_ids[unit.id] = True
        self.g.orders[unit.id] = None
        unit.idle = True
        self.run_ticks(3, walk=False)   # gives a new order
        self.g.clock = self.g.clock + 100
        self.general.player_order()     # the echo of that order
        self.assertFalse(self.general.paused(unit.id))
        self.g.clock = self.g.clock + 700
        self.general.player_order()     # too long after any order of ours: the player
        self.assertTrue(self.general.paused(unit.id))

    def test_an_order_given_while_paused_is_the_players_however_recent_ours_was(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
        self.general.player_order()     # learns that echoes come late
        self.g.selected_ids[unit.id] = True
        self.g.game_speed = 0           # the clock stands still from here on
        self.general.player_order()
        self.assertTrue(self.general.paused(unit.id))

    def test_selected_units_can_be_handed_over_and_taken_back(self):
        unit = self.player('swords', 0, 0)
        other = self.player('swords', 30, 0)
        self.enemy('swords', 0, 50)
        self.g.selected_ids[unit.id] = True
        self.assertEqual(self.general.toggle_selected(), (True, 1))
        self.run_ticks(1, walk=False)
        self.assertTrue(self.general.managed(unit.id))
        self.assertIsNotNone(self.order(unit))
        self.assertIsNone(self.order(other))
        self.assertEqual(self.general.toggle_selected(), (False, 1))
        self.run_ticks(1, walk=False)
        self.assertFalse(self.general.managed(unit.id))

    def test_a_choice_shows_before_the_units_have_followed(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.g.selected_ids[unit.id] = True
        self.general.toggle_selected()
        self.assertTrue(self.general.managed(unit.id))
        self.assertEqual(self.general.toggle_selected(), (False, 1))
        self.assertFalse(self.general.managed(unit.id))

    def test_a_unit_taken_back_stays_out_of_the_all_mode(self):
        unit = self.player('swords', 0, 0)
        other = self.player('swords', 30, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
        self.g.selected_ids[unit.id] = True
        self.general.toggle_selected()
        self.g.order_log = self.lua.table()
        self.run_ticks(4, walk=False)
        self.assertFalse(self.general.managed(unit.id))
        self.assertTrue(self.general.managed(other.id))

    def test_nothing_selected_is_reported_back(self):
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.assertIsNone(self.general.toggle_selected())

    def test_a_broken_context_only_costs_the_selection_features(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.g.context_broken = True
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.order(unit))
        self.general.player_order()
        self.assertIsNone(self.general.toggle_selected())

    def test_switching_off_returns_what_the_commander_changed(self):
        archers = self.shooter(behaviours={'fire_at_will': True, 'skirmish': False})
        self.player('swords', 0, 90)
        self.enemy('swords', 0, 92, fighting=True)
        self.go()
        self.run_ticks(1, walk=False)
        self.assertTrue(archers.behaviours.skirmish)
        self.assertFalse(archers.behaviours.fire_at_will)
        self.general.set_mode('off')
        released = archers.released
        self.run_ticks(1, walk=False)
        self.assertFalse(archers.behaviours.skirmish)
        self.assertTrue(archers.behaviours.fire_at_will)
        self.assertGreater(archers.released, released)
        self.assertFalse(self.general.managed(archers.id))

    # Who fights whom ----------------------------------------------------------------
    def test_spears_take_the_cavalry_and_swords_the_infantry(self):
        spears = self.player('spears', 0, 0)
        swords = self.player('swords', 40, 0)
        cavalry = self.enemy('knights', 0, 60)
        infantry = self.enemy('swords', 40, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(spears, cavalry)
        self.assertTargets(swords, infantry)

    def test_cavalry_does_not_charge_a_free_spear_wall(self):
        knights = self.player('knights', 0, 0, class_='cav_shk', speed=9)
        self.player('swords', 60, 0)
        wall = self.enemy('spears', 0, 60)
        archers = self.enemy('archers', 120, 90, reach=160, ammo=20, start_ammo=20, class_='inf_mis')
        self.player('swords', 62, 58, fighting=True)
        self.enemy('swords', 64, 60, fighting=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertNotEqual(self.target(knights).id if self.target(knights) else None, wall.id)
        self.assertIsNotNone(self.order(knights))
        self.assertIsNotNone(archers)

    def test_ground_troops_ignore_flyers_they_cannot_reach(self):
        unit = self.player('swords', 0, 0)
        self.enemy('dragon', 0, 20, flying=True, flies=True)
        walker = self.enemy('swords', 0, 200)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(unit, walker)

    def test_a_flyer_can_take_on_a_flyer(self):
        unit = self.player('dragon', 0, 0, flying=True, flies=True)
        foe = self.enemy('dragon', 0, 30, flying=True, flies=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(unit, foe)

    def test_units_spread_over_the_enemy_instead_of_piling_on(self):
        ours = [self.player('swords', x, 0) for x in (0, 40, 80)]
        theirs = [self.enemy('swords', x, 60) for x in (0, 40, 80)]
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual({self.target(u).id for u in ours}, {u.id for u in theirs})

    def test_a_unit_keeps_its_target_instead_of_dithering(self):
        unit = self.player('swords', 0, 0)
        first = self.enemy('swords', 0, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(unit, first)
        self.enemy('plate', 0, 55)
        self.run_ticks(3, walk=False)
        self.assertTargets(unit, first)

    def test_nobody_is_pulled_out_of_a_fight(self):
        unit = self.player('swords', 0, 0, fighting=True)
        self.enemy('swords', 0, 2, fighting=True)
        self.enemy('archers', 0, 100, reach=160, ammo=10, start_ammo=10)
        self.go('attack')
        self.run_ticks(3, walk=False)
        self.assertIsNone(self.order(unit))

    def test_a_routing_target_is_dropped_for_one_still_fighting(self):
        unit = self.player('swords', 0, 0)
        runner = self.enemy('swords', 0, 40)
        stand = self.enemy('swords', 30, 90)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(unit, runner)
        runner.routing = True
        self.run_ticks(4, walk=False)
        self.assertTargets(unit, stand)

    def test_enemies_on_our_shooters_are_dealt_with_first(self):
        guard = self.player('swords', 0, 0)
        self.shooter(x=0, z=-30, fighting=True)
        raider = self.enemy('swords', 5, -28, fighting=True)
        self.enemy('plate', 0, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(guard, raider)

    def test_cavalry_rides_around_a_fight_and_then_charges(self):
        knights = self.player('knights', 0, -120, class_='cav_shk', speed=9)
        self.player('swords', 0, 95, fighting=True)
        foe = self.enemy('swords', 0, 100, fighting=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        first = self.order(knights)
        self.assertEqual(first.kind, 'move')
        self.assertTrue(first.run)
        self.assertGreater(abs(first.x), 30)
        self.assertGreater(first.z, 100)
        widest = 0
        for _ in range(40):
            self.run_ticks(1)
            if knights.z > 80:
                widest = max(widest, abs(knights.x))
        self.assertTargets(knights, foe)
        self.assertGreater(widest, 30)    # came in from the side, not through the front

    def test_cavalry_goes_straight_in_when_the_way_around_is_blocked(self):
        knights = self.player('knights', 0, -120, class_='cav_shk', speed=9, no_path=True)
        self.player('swords', 0, 95, fighting=True)
        foe = self.enemy('swords', 0, 100, fighting=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(knights, foe)

    def test_cavalry_waits_for_the_lines_to_meet_unless_something_is_exposed(self):
        knights = self.player('knights', 100, 0, class_='cav_shk', speed=9)
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(knights))

    def test_cavalry_rides_out_for_exposed_artillery(self):
        knights = self.player('knights', 100, 0, class_='cav_shk', speed=9)
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 60)
        gun = self.enemy('mortar', 150, 300, reach=300, ammo=10, start_ammo=10, class_='art_fld')
        self.go('attack')
        self.run_ticks(1, walk=False)
        order = self.order(knights)
        self.assertTrue(order.kind == 'move' or self.target(knights).id == gun.id)

    def test_an_all_cavalry_army_does_not_wait_for_infantry(self):
        knights = self.player('knights', 0, 0, class_='cav_shk', speed=9)
        foe = self.enemy('swords', 0, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.order(knights))
        self.run_ticks(30)
        self.assertTargets(knights, foe)

    # Shooting -----------------------------------------------------------------------
    def test_archers_prefer_soft_shooters_to_shielded_armour(self):
        archers = self.shooter()
        self.enemy('plate', 0, 100)
        soft = self.enemy('archers', 30, 110, reach=160, ammo=10, start_ammo=10)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, soft)
        self.assertEqual(archers.melee_mode, False)

    def test_nobody_shoots_into_a_melee_with_friends(self):
        archers = self.shooter()
        self.player('swords', 0, 100, fighting=True)
        tied = self.enemy('swords', 0, 102, fighting=True)
        free = self.enemy('archers', 60, 140, reach=160, ammo=10, start_ammo=10)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, free)
        self.assertNotEqual(self.target(archers).id, tied.id)

    def test_fire_is_held_when_every_target_is_tied_up_with_friends(self):
        archers = self.shooter(behaviours={'fire_at_will': True})
        self.player('swords', 0, 100, fighting=True)
        self.enemy('swords', 0, 102, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(archers))
        self.assertFalse(archers.behaviours.fire_at_will)
        self.enemy('swords', 90, 130)
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(archers).kind, 'attack')
        self.run_ticks(2, walk=False)
        self.assertTrue(archers.behaviours.fire_at_will)

    def test_a_dragon_in_a_melee_can_still_be_shot(self):
        archers = self.shooter()
        self.player('swords', 0, 100, fighting=True)
        dragon = self.enemy('dragon', 0, 103, fighting=True, men=1, initial=1)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, dragon)

    def test_shooters_stop_when_their_target_gets_tied_up_with_friends(self):
        archers = self.shooter(behaviours={'fire_at_will': True})
        foe = self.enemy('swords', 0, 100)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, foe)
        self.player('swords', 0, 98, fighting=True)
        foe.fighting = True
        self.run_ticks(3, walk=False)
        self.assertEqual(self.order(archers).kind, 'halt')
        self.assertFalse(archers.behaviours.fire_at_will)

    def test_artillery_stops_shelling_what_is_tied_up_with_friends(self):
        gun = self.player('mortar', 0, 0, reach=300, ammo=20, start_ammo=20, class_='art_fld',
                          behaviours={'fire_at_will': True})
        foe = self.enemy('swords', 0, 200)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(gun, foe)
        self.player('swords', 0, 198, fighting=True)
        foe.fighting = True
        self.run_ticks(3, walk=False)
        self.assertEqual(self.order(gun).kind, 'halt')
        self.assertFalse(gun.behaviours.fire_at_will)

    def test_an_empty_gun_is_left_alone(self):
        gun = self.player('mortar', 0, 0, reach=300, ammo=0, start_ammo=20, class_='art_fld')
        self.enemy('swords', 0, 200)
        self.go('hold')
        self.run_ticks(6, walk=False)
        self.assertEqual(len(self.g.order_log), 0)
        self.assertIsNotNone(gun)

    def test_a_vehicle_with_a_gun_drives_with_the_army(self):
        tank = self.player('steam_tank', 0, 0, reach=120, ammo=20, start_ammo=20, class_='chariot')
        self.player('swords', 40, 0)
        self.enemy('swords', 0, 400)
        self.go('attack')
        self.run_ticks(60)
        self.assertGreater(tank.z, 100)

    def test_fire_at_will_does_not_flicker_as_a_target_drifts(self):
        archers = self.shooter(behaviours={'fire_at_will': True})
        friend = self.player('swords', 0, 100, fighting=True)
        self.enemy('swords', 0, 102, fighting=True)
        self.go('hold')
        for i in range(10):
            friend.z = 100 if i % 2 == 0 else 60
            self.run_ticks(1, walk=False)
        self.assertLessEqual(archers.fire_changes, 5)

    def test_a_unit_set_not_to_fire_at_will_stays_that_way(self):
        archers = self.shooter(behaviours={'fire_at_will': False})
        friend = self.player('swords', 0, 100, fighting=True)
        self.enemy('swords', 0, 102, fighting=True)
        self.go('hold')
        self.run_ticks(4, walk=False)
        friend.z = 40
        self.run_ticks(4, walk=False)
        self.assertFalse(archers.behaviours.fire_at_will)

    def test_targets_out_of_range_are_not_ordered_and_the_shooter_stays_behind_the_line(self):
        archers = self.shooter(x=0, z=-40, reach=100)
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 150)
        self.go('attack')
        self.run_ticks(60)
        self.assertLess(archers.z, 80)

    def test_artillery_never_moves_and_shoots_what_is_clear(self):
        gun = self.player('mortar', 0, 0, reach=300, ammo=20, start_ammo=20, class_='art_fld')
        blob = self.enemy('swords', 0, 200)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(gun, blob)
        self.g.reset_field()
        self.general = self.lua.execute('return NEW_GENERAL(bm, {data=DATA})')
        gun = self.player('mortar', 0, 0, reach=100, ammo=20, start_ammo=20, class_='art_fld')
        self.enemy('swords', 0, 200)
        self.go('attack')
        self.run_ticks(20)
        self.assertEqual((gun.x, gun.z), (0, 0))

    def test_artillery_shells_infantry_rather_than_a_lone_hero(self):
        gun = self.player('mortar', 0, 0, reach=300, ammo=20, start_ammo=20, class_='art_fld')
        self.enemy('wizard', 0, 150, men=1, initial=1)
        blob = self.enemy('swords', 40, 180)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(gun, blob)

    def test_a_shooter_without_ammunition_joins_the_melee(self):
        archers = self.player('archers', 0, 0, reach=160, ammo=0, start_ammo=30)
        foe = self.enemy('swords', 0, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, foe)
        self.assertEqual(archers.melee_mode, True)

    def test_hybrids_shoot_first_and_fight_when_it_gets_close(self):
        hybrid = self.player('streltsi', 0, 0, reach=120, ammo=20, start_ammo=20)
        foe = self.enemy('swords', 0, 100)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(hybrid.melee_mode, False)
        foe.z = 30
        self.run_ticks(4, walk=False)
        self.assertEqual(hybrid.melee_mode, True)

    def test_foot_archers_back_away_from_a_slow_melee_threat(self):
        archers = self.shooter(x=0, z=0)
        self.enemy('swords', 0, 20, speed=4)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(archers).kind, 'move')
        self.assertLess(self.order(archers).z, 0)

    def test_skirmish_is_switched_on_for_those_who_have_it(self):
        horse = self.shooter(behaviours={'skirmish': False, 'fire_at_will': True}, speed=8)
        self.enemy('swords', 0, 300)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTrue(horse.behaviours.skirmish)

    # The line -----------------------------------------------------------------------
    def test_a_line_marches_in_step_and_keeps_its_formation(self):
        left = self.player('swords', -60, 0, walk=3)
        right = self.player('swords', 60, 0, walk=4)
        self.enemy('swords', 0, 300)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(left).z, self.order(right).z)
        self.assertEqual(self.order(left).x, -60)
        self.assertEqual(self.order(right).x, 60)
        self.assertFalse(self.order(left).run)
        self.assertLessEqual(self.order(left).facing, 5)

    def test_marching_gives_one_order_per_step_not_one_per_tick(self):
        self.player('swords', -60, 0)
        self.player('swords', 60, 0)
        self.enemy('swords', 0, 600)
        self.go('attack')
        self.run_ticks(30)
        self.assertLessEqual(len(self.g.order_log), 2 * 3)

    def test_the_line_charges_when_it_gets_close(self):
        unit = self.player('swords', 0, 0)
        foe = self.enemy('swords', 0, 200)
        self.go('attack')
        self.run_ticks(80)
        self.assertTargets(unit, foe)
        self.assertTrue(self.order(unit).run or unit.fighting)
        self.assertEqual(self.general.phase(), 'battle')

    def test_automatic_defender_with_better_shooters_waits(self):
        unit = self.player('swords', 0, 0)
        self.shooter(x=0, z=-20)
        self.enemy('swords', 0, 400)
        self.go()
        self.run_ticks(5)
        self.assertEqual(self.general.phase(), 'hold')
        self.assertIsNone(self.order(unit))

    def test_holding_ends_by_itself_when_nothing_happens(self):
        self.player('swords', 0, 0)
        self.shooter(x=0, z=-20)
        self.enemy('swords', 0, 600)
        self.go()
        self.run_ticks(50, walk=False)
        self.assertEqual(self.general.phase(), 'advance')

    def test_automatic_attacker_advances_at_once(self):
        self.g.attacker_side = True
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 400)
        self.go()
        self.run_ticks(1, walk=False)
        self.assertEqual(self.general.phase(), 'advance')

    def test_a_much_stronger_army_advances(self):
        self.player('swords', 0, 0)
        self.player('swords', 40, 0)
        self.enemy('swords', 0, 400)
        self.go()
        self.run_ticks(1, walk=False)
        self.assertEqual(self.general.phase(), 'advance')

    def test_being_shelled_without_answer_ends_the_waiting(self):
        self.player('swords', 0, 0, shot_at=True)
        self.player('swords', 40, 0, shot_at=True)
        self.enemy('mortar', 0, 500, class_='art_fld', reach=600, ammo=10, start_ammo=10)
        self.enemy('mortar', 40, 500, class_='art_fld', reach=600, ammo=10, start_ammo=10)
        self.enemy('mortar', 80, 500, class_='art_fld', reach=600, ammo=10, start_ammo=10)
        self.go()
        self.run_ticks(2, walk=False)
        self.assertEqual(self.general.phase(), 'advance')

    def test_the_march_always_ends_in_contact_whatever_the_distance(self):
        for distance in (120, 127, 150, 160, 200, 203, 260, 333, 500):
            self.g.reset_field()
            self.g.clock = 0
            general = self.lua.execute('return NEW_GENERAL(bm, {data=DATA})')
            self.general = general
            unit = self.player('swords', 0, 0)
            foe = self.enemy('archers', 0, distance, reach=60, ammo=10, start_ammo=10, walk=0, speed=0)
            self.go('attack')
            self.run_ticks(150)
            self.assertEqual(general.phase(), 'battle', distance)
            self.assertTargets(unit, foe)

    def test_battle_goes_looking_for_enemies_that_dropped_out_of_sight(self):
        unit = self.player('swords', 0, 0)
        foe = self.enemy('swords', 0, 50)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.general.phase(), 'battle')
        foe.visible = False
        foe.z = 500
        self.g.orders[unit.id] = None
        unit.idle = True
        self.run_ticks(20)
        self.assertEqual(self.general.phase(), 'advance')
        self.assertGreater(unit.z, 30)

    def test_the_last_visible_enemies_running_off_the_map_does_not_end_the_search(self):
        unit = self.player('swords', 0, 0)
        runner = self.enemy('swords', 0, 100, routing=True)
        self.enemy('swords', 50, 700, visible=False)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.general.phase(), 'pursuit')
        runner.men = 0
        self.g.orders[unit.id] = None
        unit.idle = True
        self.run_ticks(20)
        self.assertGreater(unit.z, 30)

    def test_casters_march_with_the_army_when_nothing_is_in_sight(self):
        wizard = self.player('wizard', 0, -20, men=1, initial=1)
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 900, visible=False)
        self.go('attack')
        self.run_ticks(60)
        self.assertGreater(wizard.z, 100)

    def test_an_army_of_casters_can_still_cross_the_map(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.enemy('swords', 0, 900, visible=False)
        self.go('attack')
        self.run_ticks(60)
        self.assertGreater(wizard.z, 100)

    def test_automatic_does_not_rush_an_enemy_it_cannot_see(self):
        self.player('swords', 0, 0)
        self.enemy('dragon', 0, 900, visible=False)
        self.go()
        self.run_ticks(1, walk=False)
        self.assertEqual(self.general.phase(), 'hold')

    def test_a_routing_enemy_rallying_does_not_break_the_hold_doctrine(self):
        near = self.player('swords', 0, 0)
        foe = self.enemy('swords', 0, 300, routing=True)
        self.go('hold')
        self.run_ticks(2, walk=False)
        self.assertEqual(self.general.phase(), 'hold')
        foe.routing = False
        self.run_ticks(3, walk=False)
        self.assertEqual(self.general.phase(), 'hold')
        self.assertIsNone(self.order(near))

    def test_hold_means_hold_for_a_shooter_out_of_ammunition_too(self):
        archers = self.player('archers', 0, 0, reach=160, ammo=0, start_ammo=30)
        self.enemy('swords', 0, 400)
        self.go('hold')
        self.run_ticks(3, walk=False)
        self.assertIsNone(self.order(archers))

    def test_a_shooter_out_of_ammunition_keeps_marching_with_the_line(self):
        archers = self.player('archers', 0, 0, reach=160, ammo=0, start_ammo=30)
        self.player('swords', 40, 0)
        self.enemy('swords', 0, 500)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(archers).kind, 'move')

    def test_switching_to_hold_stops_a_marching_line(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 900)
        self.go('attack')
        self.run_ticks(1)
        self.assertEqual(self.order(unit).kind, 'move')
        self.general.cycle_doctrine()
        self.run_ticks(4)
        self.assertEqual(self.order(unit).kind, 'halt')
        count = len(self.g.order_log)
        self.run_ticks(5)
        self.assertEqual(len(self.g.order_log), count)

    def test_a_held_line_only_fights_what_comes_close(self):
        near = self.player('swords', 0, 0)
        far = self.player('swords', 400, 0)
        foe = self.enemy('swords', 0, 50)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(near, foe)
        self.assertIsNone(self.order(far))
        self.assertEqual(self.general.phase(), 'hold')

    def test_marching_direction_comes_from_enemies_out_of_sight(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 900, visible=False)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(unit).kind, 'move')
        self.assertGreater(self.order(unit).z, 0)

    def test_changing_the_doctrine_mid_march_takes_effect(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 900)
        self.go('attack')
        self.run_ticks(1)
        self.assertEqual(self.general.phase(), 'advance')
        self.general.cycle_doctrine()
        self.assertEqual(self.general.doctrine(), 'hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.general.phase(), 'hold')
        self.assertIsNotNone(unit)

    # Leaders ------------------------------------------------------------------------
    def test_a_hurt_lord_gets_out_and_comes_back_when_healed(self):
        lord = self.player('lord', 0, 0, lord=True, men=1, initial=1, hp=.2, fighting=True)
        self.player('swords', 40, 0)
        self.enemy('swords', 0, 3, fighting=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(lord).kind, 'move')
        self.assertTrue(self.order(lord).run)
        self.assertLess(self.order(lord).z, 0)
        lord.hp = .8
        lord.fighting = False
        self.run_ticks(4, walk=False)
        self.assertEqual(self.order(lord).kind, 'attack')

    def test_a_hurt_lord_stays_out_until_it_is_healed(self):
        lord = self.player('lord', 0, 0, lord=True, men=1, initial=1, hp=.2, fighting=True)
        self.player('swords', 40, 0, fighting=True)
        foe = self.enemy('swords', 0, 3, fighting=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        lord.fighting = False
        foe.fighting = False
        foe.z = 300
        self.run_ticks(30)
        self.assertEqual(self.order(lord).kind if self.order(lord) else 'none', 'none')
        self.assertLess(lord.z, 0)
        self.assertTrue(all(o.order.kind != 'attack' or o.unit != lord.id
                            for o in self.g.order_log.values()))

    def test_a_resting_lord_does_not_get_new_orders_every_few_seconds(self):
        lord = self.player('lord', 0, -60, lord=True, men=1, initial=1, hp=.2, fighting=True)
        self.player('swords', 0, 0)
        self.enemy('swords', 0, -57, fighting=True)
        self.go('attack')
        self.run_ticks(30)
        moves = [o for o in self.g.order_log.values() if o.unit == lord.id]
        self.assertLessEqual(len(moves), 6)

    def test_casters_keep_back_from_the_fighting(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1, speed=5)
        self.enemy('swords', 0, 22, speed=4)
        self.player('swords', 80, 10)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(wizard).kind, 'move')
        self.assertLess(self.order(wizard).z, 0)

    # Abilities ----------------------------------------------------------------------
    def give(self, unit, *keys):
        unit.abilities = self.lua.table_from(list(keys))

    def cast_list(self):
        return [self.g.casts[i + 1] for i in range(len(self.g.casts))]

    def test_area_spell_goes_where_the_enemy_is_dense_and_ours_are_not(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1, magic=True)
        self.give(wizard, 'smite')
        self.player('swords', -150, 130, fighting=True)
        self.enemy('swords', -150, 132, fighting=True)
        dense = self.enemy('swords', 20, 150)
        self.enemy('swords', 25, 158)
        self.enemy('swords', 30, 164)
        self.go('hold')
        self.run_ticks(1, walk=False)
        casts = self.cast_list()
        self.assertEqual(len(casts), 1)
        self.assertEqual(casts[0].key, 'smite')
        self.assertLess(abs(casts[0].target.x - dense.x), 15)

    def test_spells_wait_for_enough_winds(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'smite')
        wizard.owner.current = 3
        self.enemy('swords', 0, 150)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list(), [])

    def test_a_spell_respects_its_recharge_when_the_bar_cannot_be_read(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'bolt')
        self.enemy('wizard', 0, 200, men=1, initial=1)
        self.go('hold')
        self.run_ticks(10, walk=False)
        self.assertEqual(len(self.cast_list()), 1)
        self.run_ticks(25, walk=False)
        self.assertEqual(len(self.cast_list()), 2)

    def test_the_ability_bar_decides_when_it_can_be_read(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'bolt')
        self.enemy('wizard', 0, 200, men=1, initial=1)
        self.g.ready_text = 'something_else'
        self.go('hold')
        self.run_ticks(3, walk=False)
        self.assertEqual(self.cast_list(), [])
        self.g.ready_text = 'something_else,bolt'
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.cast_list()), 1)

    def test_limited_uses_run_out(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'once')
        self.player('swords', 0, 100, fighting=True)
        self.enemy('swords', 0, 102, fighting=True)
        self.go('hold')
        self.run_ticks(60, walk=False)
        self.assertEqual(len(self.cast_list()), 1)

    def test_overcasts_wait_for_plentiful_winds(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'smite_upgraded')
        wizard.owner.current = 25
        self.enemy('swords', 0, 150)
        self.go('hold')
        self.run_ticks(2, walk=False)
        self.assertEqual(self.cast_list(), [])
        wizard.owner.current = 40
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.cast_list()), 1)

    def test_healing_goes_to_a_hurt_friend_in_range(self):
        priest = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(priest, 'cure')
        hurt = self.player('swords', 50, 0, hp=.4)
        self.player('swords', 80, 0)
        self.enemy('swords', 0, 400)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list()[0].target.id, hurt.id)

    def test_hexes_go_on_what_our_men_are_fighting(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'curse')
        self.player('swords', 0, 100, fighting=True)
        tied = self.enemy('swords', 0, 102, fighting=True)
        self.enemy('plate', 90, 110)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list()[0].target.id, tied.id)

    def test_a_target_is_not_hexed_twice_in_a_row(self):
        for x in (0, 30):
            wizard = self.player('wizard', x, 0, men=1, initial=1)
            self.give(wizard, 'curse')
        self.player('swords', 0, 100, fighting=True)
        self.enemy('swords', 0, 102, fighting=True)
        self.go('hold')
        self.run_ticks(3, walk=False)
        self.assertEqual(len(self.cast_list()), 1)

    def test_auras_fire_when_friends_around_the_caster_are_fighting(self):
        lord = self.player('lord', 0, 0, lord=True, men=1, initial=1)
        self.give(lord, 'aura')
        self.player('swords', 15, 0, fighting=True)
        self.enemy('swords', 15, 3, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        cast = self.cast_list()[0]
        self.assertEqual(cast.key, 'aura')
        self.assertIsNone(cast.target)

    def test_self_buffs_fire_on_contact_not_before(self):
        lord = self.player('lord', 0, 0, lord=True, men=1, initial=1)
        self.give(lord, 'rage')
        foe = self.enemy('swords', 0, 300)
        self.go('hold')
        self.run_ticks(2, walk=False)
        self.assertEqual(self.cast_list(), [])
        foe.z = 20
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list()[0].key, 'rage')

    def test_ground_spells_use_the_ground_order(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'wall')
        self.enemy('swords', 0, 80)
        self.go('hold')
        self.run_ticks(1, walk=False)
        cast = self.cast_list()[0]
        self.assertEqual(cast.ground.z, 80)
        self.assertEqual(cast.bearing, 0)

    def test_summons_land_short_of_the_enemy(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'raise')
        self.enemy('swords', 0, 100)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list()[0].ground.z, 75)

    def test_rally_needs_someone_wavering(self):
        lord = self.player('lord', 0, 0, lord=True, men=1, initial=1)
        self.give(lord, 'rally_cry')
        shaken = self.player('swords', 20, 0)
        self.enemy('swords', 0, 300)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list(), [])
        shaken.wavering = True
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list()[0].key, 'rally_cry')

    def test_a_drifting_spell_keeps_clear_of_friends_by_its_whole_reach(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'drift')
        self.player('swords', 60, 150, fighting=True)
        self.enemy('swords', 60, 152, fighting=True)
        close = self.enemy('swords', 0, 150)    # 60m from a friend: inside the drift
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list(), [])
        close.x = -40                           # 100m away: clear
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.cast_list()), 1)

    def test_a_spell_that_spares_allies_may_land_next_to_them(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'harmless')
        self.player('swords', 0, 150, fighting=True)
        self.enemy('swords', 0, 152, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.cast_list()), 1)

    def test_nothing_interrupts_a_wind_up(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'slow_cast')
        self.enemy('wizard', 0, 280, men=1, initial=1)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.cast_list()), 1)
        count = len([o for o in self.g.order_log.values() if o.unit == wizard.id])
        self.run_ticks(4, walk=False)
        self.assertEqual(len([o for o in self.g.order_log.values() if o.unit == wizard.id]), count)

    def test_abilities_are_aimed_the_way_their_table_row_allows(self):
        lord = self.player('lord', 0, 0, lord=True, men=1, initial=1, fighting=True)
        self.give(lord, 'spin')
        foe = self.enemy('swords', 0, 80)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list()[0].target.id, foe.id)

    def test_an_enemy_only_self_buff_needs_an_enemy_in_range(self):
        lord = self.player('lord', 0, 0, lord=True, men=1, initial=1, fighting=True)
        self.give(lord, 'lash')
        foe = self.enemy('swords', 0, 300)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list(), [])
        foe.z = 60
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list()[0].target.id, foe.id)

    def test_a_ground_only_ability_goes_to_the_ground(self):
        lord = self.player('lord', 0, 0, lord=True, men=1, initial=1, fighting=True)
        self.give(lord, 'ground_buff')
        self.enemy('swords', 0, 60)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.cast_list()[0].ground)

    def test_a_self_and_ground_ability_reaches_a_friend_by_the_ground_order_only(self):
        lord = self.player('lord', 0, 0, lord=True, men=1, initial=1)
        self.give(lord, 'own_ward')
        self.player('swords', 50, 0, fighting=True)
        self.enemy('swords', 50, 3, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        cast = self.cast_list()[0]
        self.assertIsNone(cast.target)
        self.assertEqual(cast.ground.x, 50)

    def test_only_a_few_casts_per_second_so_the_winds_are_not_dumped(self):
        for x in range(5):
            wizard = self.player('wizard', x * 10, 0, men=1, initial=1)
            self.give(wizard, 'smite')
        for x in range(5):
            self.enemy('swords', x * 60, 150)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertLessEqual(len(self.cast_list()), 2)

    def test_abilities_the_tables_do_not_know_are_left_alone(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'unknown_transformation')
        self.enemy('swords', 0, 100)
        self.go('hold')
        self.run_ticks(3, walk=False)
        self.assertEqual(self.cast_list(), [])

    def test_free_cooldowns_let_spells_repeat(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'bolt')
        self.enemy('wizard', 0, 200, men=1, initial=1)
        self.enemy('wizard', 30, 200, men=1, initial=1)
        self.g.free_cooldowns = True
        self.go('hold')
        self.run_ticks(8, walk=False)
        self.assertEqual(len(self.cast_list()), 2)

    # After the battle ---------------------------------------------------------------
    def test_pursuit_sends_the_fast_after_the_routed(self):
        knights = self.player('knights', 0, 0, class_='cav_shk', speed=9)
        swords = self.player('swords', 40, 0, speed=4)
        runner = self.enemy('swords', 0, 100, routing=True, speed=5)
        self.go('attack')
        self.general.victory()
        self.run_ticks(1, walk=False)
        self.assertEqual(self.general.phase(), 'pursuit')
        self.assertTargets(knights, runner)
        self.assertIsNone(self.order(swords))

    def test_all_routing_counts_as_pursuit_before_the_game_says_so(self):
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 100, routing=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.general.phase(), 'pursuit')

    def test_a_late_stand_returns_to_battle(self):
        unit = self.player('swords', 0, 0)
        foe = self.enemy('swords', 0, 100, routing=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        foe.routing = False
        self.run_ticks(4, walk=False)
        self.assertEqual(self.general.phase(), 'battle')
        self.assertTargets(unit, foe)

    def test_shooters_fire_on_the_run_in_pursuit(self):
        archers = self.shooter()
        runner = self.enemy('swords', 0, 100, routing=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, runner)
        self.assertEqual(archers.melee_mode, False)

    def test_stop_gives_everything_back(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 80)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.general.stop()
        count = len(self.g.order_log)
        self.run_ticks(3, walk=False)
        self.assertEqual(len(self.g.order_log), count)
        self.assertFalse(self.general.managed(unit.id))

    # The game's own AI --------------------------------------------------------------
    def test_native_doctrine_hands_units_to_the_planner_and_back(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 60)
        self.go('native')
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.g.planner_units[unit.id])
        self.assertEqual(len(self.g.order_log), 0)
        self.assertTrue(self.general.managed(unit.id))
        self.general.cycle_doctrine()
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.g.planner_units[unit.id])
        self.assertIsNotNone(self.order(unit))

    def control_events(self, unit):
        return [e for e in self.g.control_log.values() if e.endswith(str(unit.id))]

    def assertReleasedThenRemoved(self, unit):
        events = self.control_events(unit)
        self.assertIn('remove %d' % unit.id, events)
        self.assertLess(events.index('release %d' % unit.id), events.index('remove %d' % unit.id))

    def test_leaving_the_planner_gives_control_back_on_every_path(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 500)
        self.go('native')
        self.run_ticks(1, walk=False)
        self.general.set_mode('off')           # mode off
        self.run_ticks(1, walk=False)
        self.assertReleasedThenRemoved(unit)
        self.g.control_log = self.lua.table()
        self.general.set_mode('all')
        self.run_ticks(1, walk=False)
        self.general.cycle_doctrine()          # back to the commander
        self.run_ticks(1, walk=False)
        self.assertReleasedThenRemoved(unit)
        self.g.control_log = self.lua.table()
        while self.general.doctrine() != 'native':
            self.general.cycle_doctrine()
        self.run_ticks(1, walk=False)
        self.general.stop()                    # end of battle
        self.assertReleasedThenRemoved(unit)

    def test_a_unit_the_planner_refuses_does_not_stop_the_rest(self):
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 500)
        self.go('native')
        self.g.planner_refuses = True
        self.run_ticks(4, walk=False)
        self.assertEqual(self.general.mode(), 'all')

    def test_the_native_ai_hands_over_for_the_pursuit(self):
        unit = self.player('swords', 0, 0, speed=9)
        foe = self.enemy('swords', 0, 100, routing=True, speed=5)
        self.go('native')
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.g.planner_units[unit.id])
        self.general.victory()
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.g.planner_units[unit.id])
        self.assertTargets(unit, foe)

    def test_melee_mode_is_given_back_with_the_unit(self):
        hybrid = self.player('streltsi', 0, 0, reach=120, ammo=20, start_ammo=20)
        self.enemy('swords', 0, 20)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(hybrid.melee_mode, True)
        self.general.set_mode('off')
        self.run_ticks(1, walk=False)
        self.assertEqual(hybrid.melee_mode, False)

    def test_a_unit_first_seen_by_the_planner_restores_cleanly_later(self):
        archers = self.shooter(behaviours={'fire_at_will': False})
        friend = self.player('swords', 0, 100, fighting=True)
        self.enemy('swords', 0, 102, fighting=True)
        self.go('native')
        self.run_ticks(1, walk=False)
        self.general.cycle_doctrine()
        self.run_ticks(3, walk=False)
        self.general.set_mode('off')
        self.run_ticks(1, walk=False)
        self.assertFalse(archers.behaviours.fire_at_will)
        self.assertIsNotNone(friend)

    def test_pursuit_opens_fire_again(self):
        archers = self.shooter(behaviours={'fire_at_will': True})
        self.player('swords', 0, 100, fighting=True)
        foe = self.enemy('swords', 0, 102, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertFalse(archers.behaviours.fire_at_will)
        foe.fighting = False
        foe.routing = True
        foe.z = 140
        self.general.victory()
        self.run_ticks(4, walk=False)
        self.assertTrue(archers.behaviours.fire_at_will)

    def test_walled_siege_starts_on_the_native_doctrine(self):
        siege = self.lua.execute('return NEW_GENERAL(bm, {data=DATA, walled=function() return true end})')
        self.assertEqual(siege.doctrine(), 'auto')
        siege.begin()
        self.assertEqual(siege.doctrine(), 'native')

    def test_units_owned_stay_owned_when_the_engine_stops_calling_them_the_players(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 80)
        self.go('native')
        self.run_ticks(1, walk=False)
        unit.player = False
        self.run_ticks(1, walk=False)
        self.assertTrue(self.general.owns(unit))
        self.assertIsNotNone(self.g.planner_units[unit.id])

    def test_units_leaving_the_mode_leave_the_planner(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 80)
        self.go('native')
        self.run_ticks(1, walk=False)
        self.general.set_mode('off')
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.g.planner_units[unit.id])

    # Keeping itself out of trouble --------------------------------------------------
    def test_order_rate_stays_bounded_in_a_large_battle(self):
        for i in range(40):
            self.player('swords', i * 30 - 600, 0)
            self.enemy('swords', i * 30 - 600, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertLessEqual(len(self.g.order_log), self.g.TUNE.orders_per_tick)

    def test_no_order_churn_once_everyone_has_a_job(self):
        for x in (0, 40, 80):
            self.player('swords', x, 0)
            self.enemy('plate', x, 500, walk=0, speed=0)
        self.go('attack')
        self.run_ticks(60, walk=False)
        self.assertLessEqual(len(self.g.order_log), 12)

    def test_a_mod_unit_missing_from_the_tables_still_gets_a_role(self):
        horse = self.player('modded_knights', 0, 0, class_='cav_shk', speed=9)
        foot = self.player('modded_men', 80, 0)
        foe = self.enemy('modded_men', 80, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(foot, foe)
        self.assertIsNotNone(horse)

    def test_running_with_no_tables_at_all_still_commands(self):
        general = self.lua.execute('return NEW_GENERAL(bm, {})')
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 60)
        general.set_mode('all')
        while general.doctrine() != 'attack':
            general.cycle_doctrine()
        self.g.clock = 1000
        general.tick()
        self.assertEqual(self.order(unit).kind, 'attack')

    def test_repeated_failures_stand_the_commander_down_and_report(self):
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 60)
        self.go('attack')
        self.lua.execute("function bm:get_non_player_alliance() error('engine went away') end")
        self.run_ticks(3, walk=False)
        self.assertEqual(self.general.mode(), 'off')
        self.assertIn('engine went away', self.g.reported)

    def test_a_single_failure_does_not_stop_it(self):
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 60)
        self.go('attack')
        self.lua.execute('''
            real = bm.get_non_player_alliance
            function bm:get_non_player_alliance() bm.get_non_player_alliance = real; error('hiccup') end
        ''')
        self.run_ticks(2, walk=False)
        self.assertEqual(self.general.mode(), 'all')

    def test_the_whole_thing_plays_out_without_errors(self):
        # Two armies walking into each other: nothing may throw, nobody is left standing idle.
        ours = [self.player('swords', x, 0) for x in (-80, -40, 0, 40, 80)]
        ours.append(self.shooter(x=0, z=-40, reach=150))
        ours.append(self.player('knights', 160, 0, class_='cav_shk', speed=9))
        ours.append(self.player('mortar', 0, -80, reach=300, ammo=20, start_ammo=20, class_='art_fld'))
        lord = self.player('lord', 40, -30, lord=True, men=1, initial=1)
        self.give(lord, 'aura', 'rage')
        theirs = [self.enemy('swords', x, 420) for x in (-80, -40, 0, 40, 80)]
        theirs.append(self.enemy('archers', 0, 460, reach=150, ammo=20, start_ammo=20))
        self.go()
        self.run_ticks(200)
        self.assertEqual(list(self.g.env_logs.values()).count('tick failed'), 0)
        self.assertFalse([t for t in self.g.env_logs.values() if 'failed' in t])
        self.assertEqual(self.general.phase(), 'battle')
        engaged = [u for u in ours if u.fighting or self.order(u)]
        self.assertGreaterEqual(len(engaged), len(ours) - 1)


class GeneralDataTests(unittest.TestCase):
    def test_role_tokens(self):
        self.assertEqual(role_flags('ranged_harasser_flanker', 'small', 'none'),
                         flags('ranged', 'harasser', 'flanker'))
        self.assertEqual(role_flags('flying_melee_flanker_shock_highvalue', 'large', 'none'),
                         flags('melee', 'flanker', 'shock', 'high', 'large'))
        self.assertEqual(role_flags('melee_anti_large', 'small', 'wh_missile_block_35_wood'),
                         flags('melee', 'anti_large', 'shield'))
        self.assertEqual(role_flags('ranged_indirect_arty', 'large_bst', 'none'),
                         flags('ranged', 'arty', 'large'))

    def test_every_use_is_handled_by_the_commander(self):
        text = (SRC / 'ingame/general.lua').read_text(encoding='utf-8')
        for use in USES:
            self.assertIn('function choose.' + use + '(', text)

    def test_ability_uses(self):
        def use(usage, kind='wh_type_hex', spawned=None):
            return ability_use({'ai_usage': usage, 'spawned_unit': spawned}, kind)
        self.assertEqual(use('augment_melee_self'), 'self')
        self.assertEqual(use('augment_melee_area'), 'bless')
        self.assertEqual(use('augment_ranged_area'), 'shooters')
        self.assertEqual(use('augment_defense_ranged'), 'cover')
        self.assertEqual(use('healing_area'), 'heal')
        self.assertEqual(use('hex_ranged'), 'hex_shooters')
        self.assertEqual(use('hex_armour_area'), 'hex')
        self.assertEqual(use('vortex_breath_ap'), 'nuke')
        self.assertEqual(use('direct_damage_single_entity'), 'snipe')
        self.assertEqual(use('direct_damage_single_entity_area'), 'nuke')
        self.assertEqual(use('projectile_homing_single_ap'), 'snipe')
        self.assertEqual(use('aoe_centred_on_self'), 'burst')
        self.assertEqual(use('vortex', 'wh_type_summon_unit'), 'summon')
        self.assertEqual(use(None, 'x', spawned='some_unit'), 'summon')
        self.assertIsNone(use('upgrade_weapons', 'wh_type_magic_missile'))
        self.assertIsNone(use(None, 'wh_type_transform'))

    def test_a_table_we_cannot_read_does_not_stop_the_build(self):
        import build_ingame
        from pathlib import Path
        self.assertEqual(build_ingame.general_tables(Path('Z:/missing'))['units'], {})
        with patch.object(build_ingame, 'general_data', side_effect=TypeError('layout changed')):
            self.assertEqual(build_ingame.general_tables('anything')['abilities'], {})


class LiveTableTests(unittest.TestCase):
    """Against the installed game: the numbers the commander lives on."""

    @classmethod
    def setUpClass(cls):
        from trainer_core import GAME
        cls.units = unit_profiles(GAME)

    def test_only_true_guns_stand_still(self):
        self.assertTrue(self.units['wh_main_emp_art_mortar'][0] & FLAGS['arty'])
        for vehicle in ('wh3_dlc23_chd_veh_magma_cannon', 'wh2_dlc13_emp_veh_steam_tank_driver_ror_0'):
            self.assertFalse(self.units[vehicle][0] & FLAGS['arty'], vehicle)

    def test_spell_danger_comes_from_the_vortex_table(self):
        from general_data import ability_catalog
        from trainer_core import GAME
        catalog = ability_catalog(GAME)
        drifting = catalog['wh_main_spell_fire_the_burning_head']
        self.assertGreater(drifting[9], 70)
        self.assertLessEqual(drifting[9], 100)
        bombardment = catalog['wh_main_spell_heavens_comet_of_casandora']
        self.assertEqual(bombardment[9], 50)

    def test_known_units(self):
        halberds = self.units['wh_main_emp_inf_halberdiers']
        self.assertTrue(halberds[0] & FLAGS['anti_large'])
        self.assertGreater(halberds[4], 0)
        reiksguard = self.units['wh_main_emp_cav_reiksguard']
        self.assertTrue(reiksguard[0] & FLAGS['large'] and reiksguard[0] & FLAGS['shock'])
        self.assertEqual(self.units['wh_main_dwf_inf_ironbreakers'][2], 125)
        self.assertTrue(self.units['wh_main_emp_art_mortar'][0] & FLAGS['arty'])

    def test_newest_dlc_is_covered_without_hand_written_lists(self):
        self.assertTrue(any('_dlc29_' in key for key in self.units))
