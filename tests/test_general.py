from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lupa.lua51 import LuaRuntime

from general_data import FLAGS, USES, ability_use, role_flags, unit_profiles
from tests import ROOT, SRC


def flags(*names):
    return sum(FLAGS[n] for n in names)


def row(names, value, armour, vs_large=0, vs_inf=0, block=0, attack=32, defence=32, charge=14, hp=60,
        damage=21, ap=7, interval=4.3, splash=1, morale=60):
    """A unit_profiles row, with the numbers of Empire swordsmen as the baseline."""
    return [flags(*names), value, armour, vs_large, vs_inf, block, attack, defence, charge, hp,
            damage, ap, interval, splash, morale]


UNITS = {
    'swords': row(['melee', 'shield', 'brace'], 500, 40, block=35),
    'spears': row(['melee', 'anti_large', 'shield', 'brace'], 450, 40, vs_large=16, block=30, attack=26,
                  defence=42, damage=8, ap=20, interval=5.6),
    'knights': row(['melee', 'flanker', 'shock', 'large', 'shield'], 1100, 100, block=30, attack=34, defence=31,
                   charge=62, hp=100, damage=28, ap=12, interval=5.1),
    'archers': row(['ranged'], 450, 15, attack=18, defence=18, hp=50, damage=12, ap=4),
    'guns': row(['ranged', 'direct'], 600, 20, attack=18, defence=18, hp=55, damage=12, ap=4),
    'raiders': row(['ranged', 'harasser', 'flanker', 'large'], 500, 20, attack=20, defence=22, hp=80),
    'mortar': row(['ranged', 'arty'], 700, 15, attack=10, defence=10, hp=50),
    'cannon': row(['ranged', 'arty', 'direct'], 900, 15, attack=10, defence=10, hp=50),
    'streltsi': row(['hybrid'], 800, 70, damage=9, ap=25, hp=75),
    'wizard': row(['mage', 'high'], 700, 20, attack=20, defence=20, hp=3000, damage=30, ap=10, interval=4, splash=3),
    'battle_mage': row(['melee', 'mage', 'high'], 1100, 50, attack=45, defence=35, hp=4000, damage=90, ap=160,
                       interval=4, splash=4),
    'lord': row(['melee', 'high'], 1500, 90, vs_inf=10, attack=65, defence=45, charge=65, hp=4280, damage=140,
                ap=330, interval=4, splash=5),
    'dragon': row(['melee', 'high', 'large'], 2500, 120, vs_large=20, attack=64, defence=50, charge=45, hp=9416,
                  damage=145, ap=435, interval=3.8, splash=6),
    'rider_hero': row(['melee', 'flanker', 'shock', 'high', 'large'], 1300, 90, attack=60, defence=45, charge=50,
                      hp=4000, damage=120, ap=250, interval=4, splash=4),
    'giant': row(['melee', 'large'], 1400, 50, attack=65, defence=30, charge=40, hp=12612, damage=200, ap=500,
                 interval=4, splash=7),
    'plate': row(['melee'], 900, 120, attack=46, defence=60, charge=26, hp=123, damage=34, ap=14, interval=3.8),
    'rabble': row(['melee'], 150, 0, attack=14, defence=14, hp=40, damage=10, ap=2),
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
    'storm': ability('nuke', ENEMY | GROUND, 150, 10, 13),
    'storm_upgraded': ability('nuke', ENEMY | GROUND, 150, 10, 19),
    'long_bolt': ability('snipe', ENEMY, 300, mana=5),
    'hail': ability('nuke', ENEMY, 200, 0, 8, recharge=1),
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
                         + '\nNEW_GENERAL = new_general; TUNE = GENERAL_TUNE; EDGE = general_edge\n'
                         + 'JOURNAL = GENERAL_JOURNAL; NEW_JOURNAL = new_general_journal\n')
        self.g = self.lua.globals()
        self.g.DATA = self.table({'flags': FLAGS, 'uses': list(USES), 'units': UNITS, 'abilities': ABILITIES})
        self.g.env_logs = self.lua.table()
        self.general = self.lua.execute('''
            return NEW_GENERAL(bm, {data=DATA, log=function(t) env_logs[#env_logs+1]=t end,
                report=function(t) reported=t end, free_cooldowns=function() return free_cooldowns end,
                free_winds=function() return free_winds end,
                free_fire=function() return free_fire end})
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
        self.g.clock = self.g.clock + 700
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

    def test_each_order_of_ours_comes_back_once_and_is_not_the_players(self):
        unit = self.player('swords', 0, 0)
        self.player('swords', 30, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.g.selected_ids[unit.id] = True
        self.run_ticks(1, walk=False)   # one order each
        self.g.clock = self.g.clock + 300
        self.general.player_order()
        self.general.player_order()
        self.assertFalse(self.general.paused(unit.id))
        self.general.player_order()     # nothing of ours is out any more: the player
        self.assertTrue(self.general.paused(unit.id))

    def test_abilities_come_back_like_orders(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1, magic=True)
        self.give(wizard, 'bolt')
        self.enemy('lord', 0, 200, men=1, initial=1, fighting=True)
        self.player('swords', 50, 198, fighting=True)
        self.go('hold')
        self.g.selected_ids[wizard.id] = True
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.g.casts), 1)
        self.g.clock = self.g.clock + 300
        self.general.player_order()
        self.assertFalse(self.general.paused(wizard.id))

    def test_a_click_just_before_an_echo_still_holds_the_unit(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
        self.g.selected_ids[unit.id] = True
        self.g.clock = self.g.clock + 100
        self.general.player_order()     # takes the place of the echo still on its way
        self.g.clock = self.g.clock + 200
        self.general.player_order()     # which then counts as the click
        self.assertTrue(self.general.paused(unit.id))

    def test_an_echo_that_never_comes_stops_being_waited_for(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
        self.g.selected_ids[unit.id] = True
        self.g.clock = self.g.clock + self.g.TUNE.echo_wait + 100
        self.general.player_order()
        self.assertTrue(self.general.paused(unit.id))

    def test_an_order_given_while_paused_is_the_players_however_recent_ours_was(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
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
        archers = self.shooter('guns', behaviours={'fire_at_will': True, 'skirmish': False})
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
        self.assertGreater(widest, 30)    # came in from the side, not through the front
        self.assertTrue(any(o.unit == knights.id and o.order.kind == 'attack' and o.order.target.id == foe.id
                            for o in self.g.order_log.values()))

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
        self.run_ticks(14)
        self.assertTargets(knights, foe)

    # Who the line goes for -----------------------------------------------------------
    def test_a_line_fights_the_line_and_leaves_what_is_behind_it_to_the_cavalry(self):
        ours = [self.player('swords', x, 0) for x in (0, 40, 80)]
        front = [self.enemy('swords', x, 70) for x in (0, 40, 80)]
        self.enemy('archers', 40, 100, reach=160, ammo=10, start_ammo=10)
        self.enemy('lord', 0, 95, men=1, initial=1)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual({self.target(u).id for u in ours}, {u.id for u in front})

    def test_cavalry_is_who_goes_for_what_stands_behind_the_line(self):
        self.player('swords', 0, 60, fighting=True)
        self.enemy('swords', 0, 63, fighting=True)
        knights = self.player('knights', 120, 60, class_='cav_shk', speed=9)
        archers = self.enemy('archers', 120, 130, reach=160, ammo=10, start_ammo=10)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(knights, archers)

    def test_infantry_does_not_run_after_faster_skirmishers(self):
        unit = self.player('swords', 0, 0)
        self.enemy('raiders', 0, 60, speed=9, reach=100, ammo=10, start_ammo=10, class_='cav_mis')
        foe = self.enemy('swords', 40, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(unit, foe)

    def test_a_skirmisher_within_reach_is_fair_game(self):
        unit = self.player('swords', 0, 0)
        raiders = self.enemy('raiders', 0, 30, speed=9, reach=100, ammo=10, start_ammo=10, class_='cav_mis')
        self.enemy('swords', 40, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(unit, raiders)

    def test_units_take_what_stands_in_front_of_them_rather_than_cross_paths(self):
        left = self.player('swords', -60, 0)
        right = self.player('swords', 60, 0)
        ahead_left = self.enemy('swords', -60, 70)
        ahead_right = self.enemy('swords', 60, 70)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(left, ahead_left)
        self.assertTargets(right, ahead_right)

    def test_with_a_choice_of_equal_distance_a_unit_takes_the_fight_it_wins(self):
        unit = self.player('swords', 0, 0)
        self.enemy('plate', -30, 60)
        soft = self.enemy('rabble', 30, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(unit, soft)

    def test_the_numbers_behind_a_fight(self):
        edge = self.lua.eval('''function(a_key, a_men, a_hp, e_key, e_men, e_hp)
            local function rec(key, men, strength)
                local row, s = DATA.units[key], {}
                s.attack, s.defence, s.hp, s.damage, s.ap_damage, s.interval, s.splash = row[7], row[8], row[10], row[11], row[12], row[13], row[14]
                s.armour, s.vs_large, s.vs_inf = row[3], row[4], row[5]
                s.single, s.large, s.initial = men == 1, row[1] % 512 >= 256, men
                return {s=s, men=men, strength=strength}
            end
            return EDGE(rec(a_key, a_men, a_hp), rec(e_key, e_men, e_hp))
        end''')
        self.assertGreater(edge('lord', 1, 1, 'swords', 100, 1), 2)
        self.assertLess(edge('lord', 1, 1, 'giant', 1, 1), 1)
        self.assertGreater(edge('plate', 100, 1, 'swords', 100, 1), 1.5)
        self.assertGreater(edge('swords', 100, 1, 'swords', 100, .3), 1.5)      # a beaten unit is easy
        self.assertEqual(round(edge('swords', 100, 1, 'swords', 100, 1), 5), 1)

    def test_a_lord_does_not_pick_a_fight_it_cannot_win(self):
        lord = self.player('lord', 0, 0, lord=True, men=1, initial=1)
        self.player('swords', 80, 40, fighting=True)
        self.enemy('swords', 80, 43, fighting=True)
        giant = self.enemy('giant', 0, 60, men=1, initial=1)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertNotEqual(self.target(lord).id if self.target(lord) else None, giant.id)

    def test_a_lord_goes_where_its_men_are_fighting_when_nothing_suits_it(self):
        lord = self.player('lord', 0, 0, lord=True, men=1, initial=1)
        self.player('swords', 80, 60, fighting=True)
        self.enemy('giant', 80, 63, fighting=True, men=1, initial=1)
        self.enemy('giant', 0, 40, men=1, initial=1)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(lord).kind, 'move')
        self.assertLess(abs(self.order(lord).x - 80), 30)

    def test_a_lord_takes_a_fight_it_wins(self):
        lord = self.player('lord', 0, 0, lord=True, men=1, initial=1)
        foe = self.enemy('swords', 0, 50)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(lord, foe)

    # Charging again -------------------------------------------------------------------
    def melee_knights(self, enemy='swords', **extra):
        knights = self.player('knights', 0, 0, class_='cav_shk', speed=9, fighting=True, **extra)
        foe = self.enemy(enemy, 0, 3, fighting=True)
        self.go('attack')
        return knights, foe

    def test_shock_cavalry_rides_out_of_a_melee_and_charges_again(self):
        knights, foe = self.melee_knights()
        self.run_ticks(8, walk=False)
        self.assertIsNone(self.order(knights))
        self.run_ticks(3, walk=False)
        order = self.order(knights)
        self.assertEqual(order.kind, 'move')
        self.assertTrue(order.run)
        self.assertLess(order.z, -30)
        knights.fighting = False
        foe.fighting = False
        self.run_ticks(9)
        self.assertTargets(knights, foe)
        self.assertTrue(self.order(knights).run)

    def test_spears_make_cavalry_ride_out_sooner(self):
        knights, _ = self.melee_knights('spears')
        self.run_ticks(6, walk=False)
        self.assertEqual(self.order(knights).kind, 'move')

    def test_cavalry_stays_to_finish_what_is_breaking(self):
        knights, foe = self.melee_knights()
        foe.wavering = True
        self.run_ticks(12, walk=False)
        self.assertIsNone(self.order(knights))
        foe.wavering = False
        foe.hp = .2
        self.run_ticks(12, walk=False)
        self.assertIsNone(self.order(knights))

    def test_blown_horses_stay_where_they_are(self):
        knights, _ = self.melee_knights(fatigue='threshold_exhausted')
        self.run_ticks(12, walk=False)
        self.assertIsNone(self.order(knights))

    def test_cavalry_stays_in_when_there_is_nowhere_to_ride(self):
        knights, _ = self.melee_knights(no_path=True)
        self.run_ticks(12, walk=False)
        self.assertIsNone(self.order(knights))

    def test_only_shock_cavalry_rides_out(self):
        foot = self.player('swords', 0, 0, fighting=True)
        self.enemy('swords', 0, 3, fighting=True)
        self.go('attack')
        self.run_ticks(15, walk=False)
        self.assertIsNone(self.order(foot))

    def test_cavalry_does_not_yo_yo(self):
        knights, _ = self.melee_knights()
        self.run_ticks(30, walk=False)
        moves = [o for o in self.g.order_log.values() if o.unit == knights.id and o.order.kind == 'move']
        self.assertLessEqual(len(moves), 2)

    # Who rides when -------------------------------------------------------------------
    def lines_close(self):
        """Infantry 70m apart, so the battle has begun but nobody has met anybody."""
        line = self.player('swords', 100, 0)
        foe = self.enemy('swords', 100, 70)
        return line, foe

    def mortar(self, x, z):
        return self.enemy('mortar', x, z, class_='art_fld', reach=300, ammo=20, start_ammo=20)

    def test_cavalry_does_not_ride_through_the_enemy_for_what_is_behind_it(self):
        self.lines_close()
        knights = self.player('knights', 0, 0, speed=9)
        self.enemy('spears', 0, 75)
        self.mortar(0, 300)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(knights))

    def test_a_ride_along_the_flank_is_still_open(self):
        self.lines_close()
        knights = self.player('knights', -200, 0, speed=9)
        gun = self.mortar(-300, 200)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.order(knights))
        self.assertEqual(self.order(knights).target.id if self.order(knights).kind == 'attack' else gun.id, gun.id)

    def test_flyers_go_over_what_stands_in_the_way(self):
        self.lines_close()
        knights = self.player('knights', 0, 0, speed=9, flies=True)
        self.enemy('spears', 0, 75)
        self.mortar(0, 300)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.order(knights))

    def test_cavalry_does_not_go_in_ahead_of_the_line(self):
        self.lines_close()
        knights = self.player('knights', -150, 0, speed=9)
        self.enemy('swords', -90, 70)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(knights))

    def test_only_men_on_foot_getting_stuck_in_releases_the_cavalry(self):
        self.lines_close()
        knights = self.player('knights', -150, 0, speed=9)
        other = self.player('knights', -300, 0, speed=9, fighting=True)
        self.enemy('swords', -300, 3, fighting=True)
        self.enemy('swords', -90, 70)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(knights))
        self.assertIsNone(self.order(other))

    def test_cavalry_goes_once_the_line_has_met_them(self):
        line, foe = self.lines_close()
        knights = self.player('knights', -150, 0, speed=9)
        self.go('attack')
        line.fighting, foe.fighting = True, True
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.order(knights))

    def test_cavalry_does_not_wait_for_ever(self):
        self.lines_close()
        knights = self.player('knights', -150, 0, speed=9)
        self.enemy('swords', -90, 70)
        self.go('attack')
        self.run_ticks(int(self.g.TUNE.charge_wait / 1000), walk=False)
        self.assertIsNone(self.order(knights))
        self.run_ticks(2, walk=False)
        self.assertIsNotNone(self.order(knights))

    def test_cavalry_rides_to_stop_what_is_coming_for_our_casters(self):
        self.lines_close()
        wizard = self.player('wizard', 40, -60, men=1, initial=1, magic=True)
        knights = self.player('knights', -150, 0, speed=9)
        raider = self.enemy('knights', 0, 100, speed=9, aimed_at=wizard)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.target(knights).id, raider.id)

    def test_cavalry_meets_horse_that_comes_close_but_not_spears(self):
        self.lines_close()
        knights = self.player('knights', -150, 0, speed=9)
        rider = self.enemy('knights', -150, 60, speed=9)
        self.enemy('spears', -180, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.target(knights).id, rider.id)

    def test_spears_alone_nearby_do_not_draw_the_cavalry_early(self):
        self.lines_close()
        knights = self.player('knights', -150, 0, speed=9)
        self.enemy('spears', -150, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(knights))

    # Riding down the routed ----------------------------------------------------------
    def fight_elsewhere(self):
        self.player('swords', 300, 0, fighting=True)
        return self.enemy('swords', 300, 3, fighting=True)

    def assertNotAfter(self, unit, target):
        order = self.order(unit)
        self.assertFalse(order and order.kind == 'attack' and order.target.id == target.id)

    def test_fast_units_ride_down_what_can_still_rally(self):
        self.fight_elsewhere()
        rider = self.player('knights', 0, 0, speed=9)
        runner = self.enemy('swords', 0, 60, routing=True, speed=5)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(rider, runner)
        self.assertTrue(self.order(rider).run)

    def test_the_shattered_are_left_to_run(self):
        self.fight_elsewhere()
        rider = self.player('knights', 0, 0, speed=9)
        runner = self.enemy('swords', 0, 60, routing=True, shattered=True, speed=5)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertNotAfter(rider, runner)

    def test_nobody_rides_far_after_a_runner(self):
        self.fight_elsewhere()
        rider = self.player('knights', 0, 0, speed=9)
        runner = self.enemy('swords', 0, self.g.TUNE.hunt_reach + 20, routing=True, speed=5)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertNotAfter(rider, runner)

    def test_nobody_rides_after_what_it_cannot_catch(self):
        self.fight_elsewhere()
        rider = self.player('knights', 0, 0, speed=9)
        runner = self.enemy('knights', 0, 60, routing=True, speed=9)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertNotAfter(rider, runner)

    def test_one_rider_for_each_runner(self):
        self.fight_elsewhere()
        first = self.player('knights', 0, 0, speed=9)
        second = self.player('knights', 30, 0, speed=9)
        runner = self.enemy('swords', 15, 60, routing=True, speed=5)
        self.go('attack')
        self.run_ticks(1, walk=False)
        after = [u for u in (first, second) if self.order(u) and self.order(u).kind == 'attack'
                 and self.order(u).target.id == runner.id]
        self.assertEqual(len(after), 1)

    def test_a_runner_behind_their_line_is_not_worth_the_ride(self):
        self.fight_elsewhere()
        rider = self.player('knights', 0, 0, speed=9)
        self.enemy('spears', 0, 50)
        runner = self.enemy('swords', 0, 95, routing=True, speed=5)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertNotAfter(rider, runner)

    def test_what_threatens_our_men_comes_before_any_runner(self):
        self.fight_elsewhere()
        wizard = self.player('wizard', 40, -60, men=1, initial=1, magic=True)
        rider = self.player('knights', 0, 0, speed=9)
        self.enemy('swords', 0, 60, routing=True, speed=5)
        raider = self.enemy('knights', -60, 60, speed=9, aimed_at=wizard)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.target(rider).id, raider.id)

    def test_leaders_and_men_on_foot_stay_out_of_the_hunt(self):
        self.fight_elsewhere()
        hero = self.player('rider_hero', 0, 0, speed=9, men=1, initial=1)
        foot = self.player('swords', 60, 0, speed=9)
        runner = self.enemy('swords', 30, 40, routing=True, speed=5)
        self.go('attack')
        self.run_ticks(1, walk=False)
        for unit in (hero, foot):
            self.assertNotAfter(unit, runner)

    def test_horse_archers_with_nothing_else_shoot_the_runners(self):
        raiders = self.shooter('raiders', 0, 0, speed=9)
        self.fight_elsewhere()
        runner = self.enemy('swords', 0, 80, routing=True, speed=5)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(raiders, runner)
        self.assertTrue(self.order(raiders).run)

    def test_holding_means_nobody_leaves_to_shoot_runners(self):
        raiders = self.shooter('raiders', 0, 0, speed=9)
        self.fight_elsewhere()
        self.enemy('swords', 0, 80, routing=True, speed=5)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(raiders))

    # Standing against a charge --------------------------------------------------------
    def ride(self, unit, ticks=1, step=8):
        """Ticks in which this unit covers ground, as a horse at a charge does."""
        for i in range(ticks):
            self.run_ticks(1, walk=False)
            unit.z = unit.z - step if i % 2 == 0 else unit.z + step

    def test_spears_stand_and_turn_to_face_a_charge(self):
        spears = self.player('spears', 0, 0, facing=0)
        knights = self.enemy('knights', 50, 68, speed=9, facing=220)
        self.enemy('swords', 0, 400)
        self.go('attack')
        self.ride(knights)
        self.run_ticks(1, walk=False)
        order = self.order(spears)
        self.assertEqual((order.kind, order.x, order.z), ('move', 0, 0))
        self.assertAlmostEqual(order.facing, 39.8, 0)

    def test_a_horse_standing_still_is_no_charge(self):
        spears = self.player('spears', 0, 0, facing=0)
        knights = self.enemy('knights', 0, 40, speed=9)
        self.go('attack')
        self.run_ticks(3, walk=False)
        self.assertTargets(spears, knights)

    def test_a_charge_that_never_arrives_is_given_up_on(self):
        spears = self.player('spears', 0, 0)
        knights = self.enemy('knights', 0, 40, speed=5)
        self.go('attack')
        self.ride(knights, 15)
        self.assertTargets(spears, knights)

    def test_spears_do_not_brace_against_what_is_not_coming_for_them(self):
        spears = self.player('spears', 0, 0)
        knights = self.enemy('knights', 0, 40, speed=5, facing=0)
        self.go('attack')
        self.ride(knights, 2)
        self.run_ticks(1, walk=False)
        self.assertTargets(spears, knights)

    def test_other_infantry_meets_a_charge_head_on(self):
        swords = self.player('swords', 0, 0)
        knights = self.enemy('knights', 0, 40, speed=5)
        self.go('hold')
        self.ride(knights, 2)
        self.run_ticks(1, walk=False)
        self.assertTargets(swords, knights)

    # What counts as a threat -----------------------------------------------------------
    def support_under_fire(self, shooter_key='archers', **extra):
        archers = self.shooter('archers', 0, -40)
        line = self.player('swords', 0, 0)
        front = self.enemy('swords', 0, 60)
        return line, front, self.enemy(shooter_key, 30, 130, aimed_at=archers, **extra)

    def test_bowmen_aiming_at_our_archers_do_not_draw_the_line(self):
        line, front, _ = self.support_under_fire(reach=160, ammo=30, start_ammo=30, class_='inf_mis')
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(line, front)

    def test_a_caster_aiming_at_our_archers_does_not_draw_the_line(self):
        line, front, _ = self.support_under_fire('wizard', men=1, initial=1, magic=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(line, front)

    def test_a_gun_on_our_flank_does_not_count_as_turning_it(self):
        gun = self.mortar(120, 130)
        line = self.player('swords', 0, 0, left_threat=gun)
        front = self.enemy('swords', 0, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(line, front)

    def test_what_comes_for_our_archers_on_foot_is_still_met(self):
        archers = self.shooter('archers', 0, -40)
        line = self.player('swords', 0, 0)
        self.enemy('swords', -30, 60)
        raider = self.enemy('swords', 60, 130, aimed_at=archers)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(line, raider)

    # Reacting -------------------------------------------------------------------------
    def test_a_wavering_enemy_draws_the_attack(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', -20, 60)
        shaken = self.enemy('swords', 20, 60, wavering=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(unit, shaken)

    def test_an_enemy_on_a_flank_draws_whoever_is_free(self):
        free = self.player('swords', 0, 0)
        self.enemy('swords', -30, 60)
        raider = self.enemy('swords', 30, 62)
        self.player('swords', 90, 60, right_threat=raider)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(free, raider)

    def test_something_making_for_our_shooters_is_met_before_it_gets_there(self):
        guard = self.player('swords', 0, 40)
        archers = self.shooter(x=60, z=-20)
        self.enemy('swords', -30, 100)
        raider = self.enemy('swords', 30, 102, aimed_at=archers)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(guard, raider)

    def test_threat_readings_that_fail_cost_nothing(self):
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 60)
        self.lua.execute("function broken_threat() error('not supported') end")
        unit.is_left_flank_threatened = self.g.broken_threat
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.order(unit))

    def army_of_eight(self):
        line = [self.player('swords', x, 0) for x in range(-180, 181, 60)]
        horse = self.player('knights', 0, -30, class_='cav_shk', speed=9)
        return line, horse

    def test_a_large_army_keeps_horse_and_foot_back_until_they_are_needed(self):
        line, horse = self.army_of_eight()
        self.enemy('swords', -60, 60)
        self.enemy('swords', 60, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        idle = [u for u in line + [horse] if not self.order(u)]
        self.assertEqual(len(idle), 2)
        self.assertIn(horse.id, [u.id for u in idle])

    def test_a_small_army_keeps_nothing_back(self):
        ours = [self.player('swords', x, 0) for x in (0, 40, 80)]
        self.enemy('swords', 0, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTrue(all(self.order(u) for u in ours))

    def test_the_reserve_goes_in_when_the_enemy_turns_a_flank(self):
        line, horse = self.army_of_eight()
        foe = self.enemy('swords', -60, 60)
        self.enemy('swords', 60, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        kept = [u for u in line if not self.order(u)][0]
        line[0].left_threat = foe
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.order(kept))

    def test_the_reserve_goes_in_at_the_latest_after_a_minute(self):
        line, _ = self.army_of_eight()
        self.enemy('swords', -60, 60)
        self.enemy('swords', 60, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        kept = [u for u in line if not self.order(u)][0]
        self.run_ticks(62, walk=False)
        self.assertIsNotNone(self.order(kept))

    def test_a_reserve_that_gets_attacked_fights(self):
        line, _ = self.army_of_eight()
        self.enemy('swords', -60, 60)
        self.enemy('swords', 60, 60)
        self.go('attack')
        self.run_ticks(1, walk=False)
        kept = [u for u in line if not self.order(u)][0]
        kept.fighting = True
        self.run_ticks(2, walk=False)
        kept.fighting = False
        self.run_ticks(4, walk=False)
        self.assertIsNotNone(self.order(kept))

    # Shooting -----------------------------------------------------------------------
    def test_shooters_stay_put_while_the_enemy_nearby_is_busy_with_our_line(self):
        archers = self.shooter(x=0, z=-25)
        self.player('swords', 0, 0, fighting=True)
        foe = self.enemy('swords', 0, 3, fighting=True, speed=4)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, foe)

    def test_a_cannon_goes_for_the_dragon_and_a_mortar_for_the_block(self):
        cannon = self.player('cannon', 0, 0, reach=400, ammo=20, start_ammo=20, class_='art_fld')
        mortar = self.player('mortar', 40, 0, reach=400, ammo=20, start_ammo=20, class_='art_fld')
        dragon = self.enemy('dragon', -20, 250, men=1, initial=1)
        block = self.enemy('swords', 60, 250)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(cannon, dragon)
        self.assertTargets(mortar, block)

    def test_archers_prefer_soft_shooters_to_shielded_armour(self):
        archers = self.shooter()
        self.enemy('plate', 0, 100)
        soft = self.enemy('archers', 30, 110, reach=160, ammo=10, start_ammo=10)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, soft)
        self.assertEqual(archers.melee_mode, False)

    def test_a_free_target_comes_before_one_tied_up_with_friends(self):
        archers = self.shooter()
        self.player('swords', 0, 100, fighting=True)
        tied = self.enemy('swords', 0, 102, fighting=True)
        free = self.enemy('archers', 60, 140, reach=160, ammo=10, start_ammo=10)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, free)
        self.assertNotEqual(self.target(archers).id, tied.id)

    def test_arcing_shots_carry_on_into_a_melee_from_behind_our_line(self):
        archers = self.shooter(behaviours={'fire_at_will': True})
        self.player('swords', 0, 100, fighting=True)
        tied = self.enemy('swords', 0, 102, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, tied)
        self.assertTrue(archers.behaviours.fire_at_will)

    def test_flat_shots_never_go_through_our_own_men(self):
        guns = self.shooter('guns', reach=150)
        self.player('swords', 0, 60)
        free = self.enemy('swords', 0, 120)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertNotEqual(self.target(guns).id if self.target(guns) else None, free.id)

    def test_guns_walk_along_the_line_for_an_open_shot(self):
        guns = self.shooter('guns', x=10, z=-10, reach=150, behaviours={'fire_at_will': True})
        self.player('swords', 0, 60, fighting=True)
        foe = self.enemy('swords', 0, 63, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(guns).kind, 'move')
        self.assertFalse(guns.behaviours.fire_at_will)
        self.run_ticks(45)
        self.assertTargets(guns, foe)
        self.assertGreater(guns.x, 60)

    def test_fire_comes_back_on_when_a_target_opens_up(self):
        archers = self.shooter('guns', behaviours={'fire_at_will': True})
        self.player('swords', 0, 100, fighting=True)
        self.enemy('swords', 0, 102, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertFalse(archers.behaviours.fire_at_will)
        self.enemy('swords', 90, 130)
        self.run_ticks(3, walk=False)
        self.assertEqual(self.order(archers).kind, 'attack')
        self.assertTrue(archers.behaviours.fire_at_will)

    def test_a_dragon_in_a_melee_can_still_be_shot(self):
        archers = self.shooter()
        self.player('swords', 0, 100, fighting=True)
        dragon = self.enemy('dragon', 0, 103, fighting=True, men=1, initial=1)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, dragon)

    def test_guns_stop_when_friends_step_into_the_line_of_fire(self):
        archers = self.shooter('guns', behaviours={'fire_at_will': True})
        foe = self.enemy('swords', 0, 100)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, foe)
        self.player('swords', 0, 98, fighting=True)
        foe.fighting = True
        self.run_ticks(1, walk=False)
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

    # Friendly fire off ---------------------------------------------------------------
    def test_with_friendly_fire_off_flat_shots_go_straight_past_our_men(self):
        self.g.free_fire = True
        guns = self.shooter('guns', reach=150, behaviours={'fire_at_will': True})
        self.player('swords', 0, 60)
        foe = self.enemy('swords', 0, 120)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(guns, foe)
        self.assertTrue(guns.behaviours.fire_at_will)

    def test_with_friendly_fire_off_nobody_walks_for_an_angle(self):
        self.g.free_fire = True
        guns = self.shooter('guns', x=10, z=-10, reach=150, behaviours={'fire_at_will': True})
        self.player('swords', 0, 60, fighting=True)
        foe = self.enemy('swords', 0, 63, fighting=True)
        self.go('hold')
        self.run_ticks(5, walk=False)
        self.assertTargets(guns, foe)
        self.assertEqual((guns.x, guns.z), (10, -10))

    def test_with_friendly_fire_off_shells_land_in_a_melee(self):
        self.g.free_fire = True
        gun = self.player('mortar', 0, 0, reach=300, ammo=20, start_ammo=20, class_='art_fld',
                          behaviours={'fire_at_will': True})
        self.player('swords', 0, 198, fighting=True)
        foe = self.enemy('swords', 0, 200, fighting=True)
        self.go('hold')
        self.run_ticks(3, walk=False)
        self.assertTargets(gun, foe)
        self.assertTrue(gun.behaviours.fire_at_will)

    def melee_or_open_target(self):
        archers = self.shooter()
        self.player('swords', 0, 100, fighting=True)
        tied = self.enemy('swords', 0, 102, fighting=True)
        free = self.enemy('swords', 60, 140)
        self.go('hold')
        self.run_ticks(1, walk=False)
        return archers, tied, free

    def test_friends_next_to_a_target_normally_count_against_it(self):
        archers, _, free = self.melee_or_open_target()
        self.assertTargets(archers, free)

    def test_with_friendly_fire_off_they_do_not(self):
        self.g.free_fire = True
        archers, tied, _ = self.melee_or_open_target()
        self.assertTargets(archers, tied)

    def test_switching_friendly_fire_off_mid_battle_opens_fire_again(self):
        guns = self.shooter('guns', behaviours={'fire_at_will': True})
        self.player('swords', 0, 100, fighting=True)
        foe = self.enemy('swords', 0, 102, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertFalse(guns.behaviours.fire_at_will)
        self.g.free_fire = True
        self.run_ticks(3, walk=False)
        self.assertTargets(guns, foe)
        self.assertTrue(guns.behaviours.fire_at_will)

    def test_switching_it_back_stops_shots_through_our_men(self):
        self.g.free_fire = True
        guns = self.shooter('guns', behaviours={'fire_at_will': True})
        self.player('swords', 0, 100, fighting=True)
        self.enemy('swords', 0, 102, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.g.free_fire = False
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(guns).kind, 'halt')
        self.assertFalse(guns.behaviours.fire_at_will)

    def test_spells_keep_clear_of_friends_with_friendly_fire_off_too(self):
        self.g.free_fire = True
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'drift')
        self.player('swords', 60, 150, fighting=True)
        self.enemy('swords', 60, 152, fighting=True)
        self.enemy('swords', 0, 150)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list(), [])

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
        archers = self.shooter('guns', behaviours={'fire_at_will': True})
        friend = self.player('swords', 0, 100, fighting=True)
        self.enemy('swords', 0, 102, fighting=True)
        self.go('hold')
        for i in range(10):
            friend.z = 100 if i % 2 == 0 else 60
            self.run_ticks(1, walk=False)
        self.assertLessEqual(archers.fire_changes, 5)

    def test_a_unit_set_not_to_fire_at_will_stays_that_way(self):
        archers = self.shooter('guns', behaviours={'fire_at_will': False})
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
        self.run_ticks(100, walk=False)
        self.assertEqual(self.general.phase(), 'hold')
        self.run_ticks(60, walk=False)
        self.assertEqual(self.general.phase(), 'advance')

    def test_holding_lasts_as_long_as_the_enemy_keeps_coming(self):
        self.player('swords', 0, 0)
        self.shooter(x=0, z=-20)
        foe = self.enemy('swords', 0, 900)
        self.go()
        for _ in range(100):
            foe.z = foe.z - 5
            self.run_ticks(3, walk=False)
            foe.z = foe.z - 2
        self.assertEqual(self.general.phase(), 'hold')

    def test_holding_lasts_as_long_as_our_shooters_have_something_to_do(self):
        self.player('swords', 0, 0)
        self.shooter(x=0, z=-20, reach=400)
        self.enemy('swords', 0, 300)
        self.go()
        self.run_ticks(200, walk=False)
        self.assertEqual(self.general.phase(), 'hold')

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

    def test_a_skirmisher_out_in_front_does_not_slow_the_march(self):
        unit = self.player('swords', 0, 0, walk=3)
        self.enemy('raiders', 0, 120, speed=9, reach=100, ammo=10, start_ammo=10, class_='cav_mis')
        self.enemy('swords', 0, 700)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(unit).z, 45)

    def test_horsemen_out_in_front_do_not_stop_the_march_either(self):
        unit = self.player('swords', 0, 0, walk=3)
        self.enemy('knights', 200, 120, speed=9)
        self.enemy('swords', 0, 700)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(unit).kind, 'move')
        self.assertEqual(self.general.phase(), 'advance')

    def test_something_about_to_hit_the_column_is_met(self):
        unit = self.player('swords', 0, 0)
        knights = self.enemy('knights', 0, 40, speed=5)
        self.enemy('swords', 0, 700)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(unit, knights)

    def test_stragglers_run_to_catch_up(self):
        self.player('swords', 0, 400)
        late = self.player('swords', 0, 0)
        self.enemy('swords', 0, 900)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTrue(self.order(late).run)
        self.assertGreater(self.order(late).z, 250)

    def test_stragglers_run_into_a_battle_too(self):
        self.player('swords', 0, 400, fighting=True)
        self.enemy('swords', 0, 403, fighting=True)
        late = self.player('swords', 0, 0)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTrue(self.order(late).run)

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
        foe = self.enemy('swords', 0, 40)
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
        self.assertEqual(cast.target.id, lord.id)

    def test_a_refused_ability_costs_that_ability_and_not_the_unit(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1, magic=True)
        self.give(wizard, 'aura', 'bolt')
        self.g.refused_abilities.aura = True
        self.player('swords', 15, 0, fighting=True)
        self.enemy('swords', 15, 3, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual([c.key for c in self.cast_list()], ['bolt'])
        self.assertEqual(self.general.mode(), 'all')
        self.g.ready_text = 'aura,bolt'
        refusals = lambda: [t for t in self.g.env_logs.values() if 'refused' in t]
        self.assertIn('aura', refusals()[0])
        self.run_ticks(5, walk=False)
        self.assertEqual(len(refusals()), 1)

    def test_a_refused_ability_is_tried_again_after_a_while(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1, magic=True)
        self.give(wizard, 'aura')
        self.g.refused_abilities.aura = True
        self.player('swords', 15, 0, fighting=True)
        self.enemy('swords', 15, 3, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.g.refused_abilities.aura = None
        self.run_ticks(int(self.g.TUNE.refused_gap / 1000) + 2, walk=False)
        self.assertEqual([c.key for c in self.cast_list()][:1], ['aura'])

    def test_a_unit_refusing_an_ability_can_still_be_given_orders(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1, magic=True, speed=6)
        self.give(wizard, 'aura')
        self.g.refused_abilities.aura = True
        self.player('swords', 15, 0, fighting=True)
        self.enemy('swords', 15, 3, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        raider = self.enemy('knights', 0, 30, speed=9)
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(raider)
        self.assertEqual(self.order(wizard).kind, 'move')

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

    def test_a_caster_stands_where_its_shortest_attack_spell_reaches(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'long_bolt', 'storm')
        self.player('swords', 0, 300)
        self.enemy('swords', 0, 400)
        self.go('attack')
        self.run_ticks(3, walk=False)
        self.g.orders[wizard.id] = None
        wizard.idle = True
        self.run_ticks(4, walk=False)
        self.assertGreater(self.order(wizard).z, 230)

    def test_a_caster_far_behind_the_line_runs_to_get_its_spells_in_before_contact(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'storm')
        self.player('swords', 0, 200, fighting=True)
        self.enemy('swords', 0, 203, fighting=True)
        self.go('attack')
        self.run_ticks(2, walk=False)
        self.g.orders[wizard.id] = None
        wizard.idle = True
        self.run_ticks(4, walk=False)
        self.assertTrue(self.order(wizard).run)

    def test_a_caster_that_can_fight_is_still_a_caster(self):
        mage = self.player('battle_mage', 0, 0, men=1, initial=1)
        self.give(mage, 'storm')
        self.player('swords', 0, 200, fighting=True)
        foe = self.enemy('plate', 0, 40)
        self.enemy('swords', 0, 203, fighting=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertNotEqual(self.target(mage).id if self.target(mage) else None, foe.id)

    def test_a_caster_that_can_fight_takes_on_what_it_clearly_beats(self):
        mage = self.player('battle_mage', 0, 0, men=1, initial=1)
        self.player('swords', 80, 0, fighting=True)
        self.enemy('swords', 80, 3, fighting=True)
        rabble = self.enemy('rabble', 0, 20)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertTargets(mage, rabble)

    def test_a_hurt_caster_does_not_join_in(self):
        mage = self.player('battle_mage', 0, 0, men=1, initial=1, hp=.5)
        self.player('swords', 80, 0, fighting=True)
        self.enemy('swords', 80, 3, fighting=True)
        rabble = self.enemy('rabble', 0, 20)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertNotEqual(self.target(mage).id if self.target(mage) else None, rabble.id)

    def hail_of_spells(self, winds):
        for i in range(4):
            wizard = self.player('wizard', i * 10, 0, men=1, initial=1)
            self.give(wizard, 'hail')
            wizard.owner.current = winds
        for x in (-60, -50, 0, 10, 60, 70):
            self.enemy('swords', x, 150)
        self.go('hold')

    def test_winds_are_spent_over_time_not_all_at_once(self):
        self.hail_of_spells(30)
        self.run_ticks(5, walk=False)
        self.assertEqual(len(self.cast_list()), 1)     # 8 of a budget of 12, a second one would overdraw
        self.run_ticks(9, walk=False)
        self.assertEqual(len(self.cast_list()), 2)

    def test_a_full_pool_may_be_spent_faster(self):
        self.hail_of_spells(100)
        self.run_ticks(5, walk=False)
        self.assertEqual(len(self.cast_list()), 5)

    def test_unlimited_winds_lift_the_budget(self):
        self.g.free_winds = True
        self.hail_of_spells(30)
        self.run_ticks(5, walk=False)
        self.assertGreater(len(self.cast_list()), 4)

    def test_overcasts_come_first_when_the_winds_are_plentiful(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'storm', 'storm_upgraded')
        wizard.owner.current = 60
        self.enemy('swords', 0, 100)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list()[0].key, 'storm_upgraded')

    def test_the_plain_version_comes_first_otherwise(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'storm', 'storm_upgraded')
        wizard.owner.current = 25
        self.enemy('swords', 0, 100)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list()[0].key, 'storm')

    def test_single_target_spells_go_on_ordinary_units_when_the_winds_can_bear_it(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'bolt')
        self.enemy('swords', 0, 200)
        wizard.owner.current = 10
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list(), [])
        wizard.owner.current = 25
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.cast_list()), 1)

    def test_hexes_reach_out_for_big_things_coming_at_us(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'curse')
        self.player('swords', 0, 30)
        giant = self.enemy('giant', 0, 110, men=1, initial=1)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list()[0].target.id, giant.id)

    def test_with_a_choice_of_targets_a_blast_has_to_catch_more_than_one(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1)
        self.give(wizard, 'smite')
        for x in (-150, -60, 60, 150):
            self.enemy('swords', x, 120)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.cast_list(), [])
        self.enemy('swords', 65, 125)
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.cast_list()), 1)

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
        archers = self.shooter('guns', behaviours={'fire_at_will': False})
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
        archers = self.shooter('guns', behaviours={'fire_at_will': True})
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

    # Casters and their cover --------------------------------------------------------
    def test_cavalry_far_ahead_does_not_pull_the_casters_forward(self):
        wizard = self.player('wizard', 0, -100, men=1, initial=1, magic=True)
        self.player('swords', 0, 0)
        self.player('knights', 0, 250)
        self.enemy('swords', 0, 300)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(wizard).kind, 'move')
        self.assertAlmostEqual(self.order(wizard).z, -self.g.TUNE.caster_setback, 0)

    def test_an_army_of_riders_still_has_a_front(self):
        self.player('knights', 0, 0)
        self.player('knights', 0, 230)
        self.enemy('swords', 0, 290)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.general.phase(), 'battle')

    def test_a_caster_who_cannot_fight_leaves_a_melee_for_cover(self):
        wizard = self.player('wizard', -50, 20, men=1, initial=1, magic=True, fighting=True)
        line = self.player('swords', 100, 0)
        self.enemy('knights', -50, 22, fighting=True)
        self.enemy('swords', 100, 70)
        self.go('attack')
        self.run_ticks(1, walk=False)
        order = self.order(wizard)
        self.assertEqual((order.kind, order.run), ('move', True))
        self.assertLess(abs(order.x - line.x), 40)
        self.assertLess(order.z, line.z)

    def test_a_caster_makes_for_cover_before_it_is_caught(self):
        wizard = self.player('wizard', -50, 0, men=1, initial=1, magic=True, speed=5)
        self.player('swords', 100, 0)
        self.enemy('knights', -50, 40, speed=9)
        self.enemy('swords', 100, 70)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual((self.order(wizard).kind, self.order(wizard).run), ('move', True))

    def test_a_caster_already_behind_its_men_stays_and_casts(self):
        wizard = self.player('wizard', 0, -30, men=1, initial=1, magic=True, fighting=True)
        self.give(wizard, 'curse')
        self.player('swords', 0, 0, fighting=True)
        self.enemy('swords', 0, 3, fighting=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(wizard))
        self.assertEqual(len(self.g.casts), 1)

    def test_a_caster_that_can_fight_stays_in_the_melee(self):
        mage = self.player('battle_mage', -50, 20, men=1, initial=1, magic=True, fighting=True)
        self.player('swords', 100, 0)
        self.enemy('knights', -50, 22, fighting=True)
        self.enemy('swords', 100, 70)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(mage))

    def test_a_lone_caster_still_gets_away(self):
        wizard = self.player('wizard', 0, 0, men=1, initial=1, magic=True, fighting=True)
        self.enemy('knights', 0, 3, fighting=True)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertLess(self.order(wizard).z, 0)

    def test_no_spell_on_the_way_out(self):
        wizard = self.player('wizard', -50, 20, men=1, initial=1, magic=True, fighting=True)
        self.give(wizard, 'curse')
        self.player('swords', 100, 0)
        self.enemy('knights', -50, 22, fighting=True)
        self.enemy('swords', 100, 70)
        self.go('attack')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(wizard).kind, 'move')
        self.assertEqual(len(self.g.casts), 0)
        wizard.idle = True
        self.g.order_log = self.lua.table()
        wizard.x, wizard.z = 0, -10
        wizard.fighting = False
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.g.casts), 1)

    # Men on foot with a bow ------------------------------------------------------------
    def test_foot_archers_leave_riders_to_fire_at_will(self):
        archers = self.shooter('archers', behaviours={'fire_at_will': True})
        rider = self.enemy('knights', 0, 100, speed=9)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, rider)
        rider.x = 12
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(archers).kind, 'halt')
        self.assertTrue(archers.behaviours.fire_at_will)

    def test_foot_archers_stay_on_what_stands_or_walks(self):
        archers = self.shooter('archers', behaviours={'fire_at_will': True})
        foe = self.enemy('swords', 0, 100)
        self.go('hold')
        self.run_ticks(1, walk=False)
        foe.z = 97
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, foe)

    def test_horse_archers_may_aim_at_what_rides(self):
        raiders = self.shooter('raiders', speed=9)
        rider = self.enemy('knights', 0, 170, speed=9)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.order(raiders))
        rider.z = 150
        self.run_ticks(1, walk=False)
        self.assertTargets(raiders, rider)

    def test_a_target_walking_out_of_range_is_not_walked_after(self):
        archers = self.shooter('archers', reach=160)
        foe = self.enemy('swords', 0, 155)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertTargets(archers, foe)
        foe.z = 170
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(archers).kind, 'halt')

    def test_a_shooter_dragged_along_by_a_melee_stops_following(self):
        archers = self.shooter('archers')
        self.enemy('knights', 0, 100, speed=9)
        self.go('hold')
        self.run_ticks(1, walk=False)
        archers.fighting = True
        archers.z = 8
        self.g.clock = self.g.clock + 2500
        self.run_ticks(1, walk=False)
        self.assertEqual(self.order(archers).kind, 'halt')

    def test_a_shooter_standing_in_a_melee_is_left_to_it(self):
        archers = self.shooter('archers')
        self.enemy('knights', 0, 100, speed=9)
        self.go('hold')
        self.run_ticks(1, walk=False)
        archers.fighting = True
        self.run_ticks(3, walk=False)
        self.assertEqual(self.order(archers).kind, 'attack')

    def test_a_shooter_gone_silent_changes_target(self):
        archers = self.shooter('archers')
        self.enemy('swords', -40, 100)
        self.enemy('swords', 40, 100)
        self.go('hold')
        self.run_ticks(1, walk=False)
        first = self.target(archers).id
        self.run_ticks(int(self.g.TUNE.silent_limit / 1000) + 1, walk=False)
        self.assertNotEqual(self.target(archers).id, first)

    def test_a_shooter_using_up_arrows_keeps_its_target(self):
        archers = self.shooter('archers')
        archers.ammo, archers.start_ammo = 500, 500
        self.enemy('swords', -40, 100)
        self.enemy('swords', 40, 100)
        self.go('hold')
        self.run_ticks(1, walk=False)
        first = self.target(archers).id
        for _ in range(30):
            archers.ammo = archers.ammo - 1
            self.run_ticks(1, walk=False)
        self.assertEqual(self.target(archers).id, first)

    def test_a_shooter_with_endless_arrows_counts_its_hits(self):
        archers = self.shooter('archers')
        self.enemy('swords', -40, 100)
        self.enemy('swords', 40, 100)
        self.go('hold')
        self.run_ticks(1, walk=False)
        first = self.target(archers).id
        for tick in range(30):
            archers.kills = tick
            self.run_ticks(1, walk=False)
        self.assertEqual(self.target(archers).id, first)

    def test_the_only_target_going_silent_leaves_them_where_they_are(self):
        archers = self.shooter('archers')
        self.enemy('swords', 0, 100)
        self.go('hold')
        self.run_ticks(int(self.g.TUNE.silent_limit / 1000) + 2, walk=False)
        self.assertEqual(self.order(archers).kind, 'halt')

    def test_a_silent_target_is_given_another_chance_later(self):
        archers = self.shooter('archers')
        foe = self.enemy('swords', 0, 100)
        self.go('hold')
        self.run_ticks(int((self.g.TUNE.silent_limit + self.g.TUNE.silent_skip) / 1000) + 3, walk=False)
        self.assertTargets(archers, foe)

    # Walled sieges ------------------------------------------------------------------
    def siege(self, walled=True):
        self.g.walled_battle = walled
        self.general = self.lua.execute('''
            return NEW_GENERAL(bm, {data=DATA, walled=function() return walled_battle end})
        ''')

    def test_a_walled_siege_is_left_to_the_player(self):
        self.siege()
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 40)
        self.go()
        self.run_ticks(3, walk=False)
        self.assertEqual(len(self.g.order_log), 0)
        self.assertEqual(self.g.planner_calls, 0)
        self.assertFalse(self.general.managed(unit.id))
        self.assertEqual(self.general.doctrine(), 'auto')
        self.assertIn('原生 AI', self.general.status())

    def test_an_open_field_is_commanded_as_ever(self):
        self.siege(walled=False)
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 40)
        self.go()
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.order(unit))

    def test_no_scripted_doctrine_opens_a_walled_siege(self):
        self.siege()
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 40)
        for doctrine in ('attack', 'hold'):
            self.go(doctrine)
            self.run_ticks(2, walk=False)
            self.assertEqual(len(self.g.order_log), 0)
            self.assertEqual(self.g.planner_calls, 0)

    def test_picking_the_native_ai_takes_a_walled_siege_and_leaving_it_gives_it_back(self):
        self.siege()
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 40)
        self.go('native')
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.g.planner_units[unit.id])
        self.assertTrue(self.general.managed(unit.id))
        self.general.cycle_doctrine()
        self.assertEqual(self.general.doctrine(), 'auto')
        self.run_ticks(1, walk=False)
        self.assertReleasedThenRemoved(unit)
        self.assertFalse(self.general.managed(unit.id))
        self.assertEqual(len(self.g.order_log), 0)

    def test_handing_units_over_is_refused_in_a_walled_siege(self):
        self.siege()
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 40)
        self.g.selected_ids[unit.id] = True
        given, text = self.general.toggle_selected()
        self.assertIsNone(given)
        self.assertIn('原生 AI', text)
        self.assertFalse(self.general.managed(unit.id))

    def test_units_picked_for_the_native_ai_do_not_stay_marked_after_it(self):
        self.siege()
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 40)
        self.go('native')
        self.general.set_mode('off')
        self.g.selected_ids[unit.id] = True
        self.assertEqual(self.general.toggle_selected(), (True, 1))
        self.run_ticks(1, walk=False)
        self.assertIsNotNone(self.g.planner_units[unit.id])
        self.general.cycle_doctrine()
        self.run_ticks(1, walk=False)
        self.assertIsNone(self.g.planner_units[unit.id])
        self.assertFalse(self.general.managed(unit.id))

    def test_a_won_walled_siege_is_still_chased(self):
        self.siege()
        unit = self.player('swords', 0, 0, speed=9)
        foe = self.enemy('swords', 0, 100, routing=True, speed=5)
        self.go()
        self.run_ticks(1, walk=False)
        self.assertEqual(len(self.g.order_log), 0)
        self.general.victory()
        self.run_ticks(1, walk=False)
        self.assertTargets(unit, foe)
        self.assertEqual(self.g.planner_calls, 0)

    # The journal --------------------------------------------------------------------
    def journalled(self, name='general.log'):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / name
        self.g.journal_path = str(path)
        self.general = self.lua.execute('return NEW_GENERAL(bm, {data=DATA, journal_file=journal_path})')
        return path

    @staticmethod
    def rows(path, event):
        lines = path.read_text(encoding='utf-8').splitlines()
        rows = [line.split('\t') for line in lines]
        return [dict(part.split('=', 1) for part in row[2:]) for row in rows if row[1] == event]

    def test_the_journal_is_on_disk_after_every_tick(self):
        path = self.journalled()
        unit = self.player('swords', 0, 0)
        foe = self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
        battle = self.rows(path, 'battle')[0]
        self.assertEqual((battle['mode'], battle['doctrine'], battle['type']), ('all', 'auto', 'land_normal'))
        self.assertEqual((battle['friends'], battle['enemies']), ('1', '1'))
        order = self.rows(path, 'order')[0]
        self.assertEqual((order['unit'], order['kind'], order['key']), (str(unit.id), 'attack', 'swords'))
        self.assertEqual(order['target'], str(foe.id))

    def test_nothing_is_written_while_the_commander_is_off(self):
        path = self.journalled()
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.run_ticks(3, walk=False)
        self.general.stop()
        self.assertFalse(path.exists())

    def test_the_whole_field_is_pictured_every_five_seconds(self):
        path = self.journalled()
        unit = self.player('swords', 0, 0)
        foe = self.enemy('swords', 0, 50, hp=.5, kills=7)
        self.go()
        self.run_ticks(11, walk=False)
        self.assertEqual(len(self.rows(path, 'field')), 3)
        units = self.rows(path, 'unit')
        self.assertEqual(len(units), 6)
        ours, theirs = units[-2], units[-1]
        self.assertEqual((ours['side'], ours['id'], ours['ai']), ('us', str(unit.id), 'true'))
        self.assertEqual((ours['order'], ours['target']), ('attack', str(foe.id)))
        self.assertEqual((theirs['side'], theirs['hp'], theirs['kills']), ('them', '0.50', '7'))
        self.assertNotIn('ai', theirs)

    def test_a_walled_siege_is_on_file_too(self):
        path = self.journalled()
        self.lua.execute('''
            local name = journal_path
            journalled_siege = NEW_GENERAL(bm, {data=DATA, journal_file=name, walled=function() return true end})
        ''')
        self.general = self.g.journalled_siege
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(2, walk=False)
        self.assertEqual(self.rows(path, 'battle')[0]['walled'], 'true')
        self.assertIn('原生 AI', self.rows(path, 'field')[0]['status'])

    def test_a_unit_going_to_the_planner_is_on_file_before_the_call(self):
        path = self.journalled()
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        seen = []
        self.g.planner_hook = lambda _: seen.append(len(self.rows(path, 'planner')))
        self.go('native')
        self.run_ticks(1, walk=False)
        self.assertEqual(seen, [1])

    def test_echoes_and_player_orders_are_told_apart(self):
        path = self.journalled()
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.g.echo_handler = self.general.player_order
        self.g.echo_mode = 'sync'
        self.go()
        self.run_ticks(1, walk=False)
        self.assertEqual(self.rows(path, 'echo')[0]['when'], 'inside')
        self.g.echo_mode = None
        self.g.clock = self.g.clock + 150
        self.general.player_order()     # nothing selected: an echo, a moment late
        self.g.selected_ids[unit.id] = True
        self.g.clock = self.g.clock + 700
        self.general.player_order()     # the player's
        self.run_ticks(1, walk=False)
        self.assertEqual(self.rows(path, 'player')[0], {'selected': '1', 'taken': '1'})

    def test_an_echo_on_time_is_on_file_with_its_delay(self):
        path = self.journalled()
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
        self.g.clock = self.g.clock + 300
        self.general.player_order()
        self.run_ticks(1, walk=False)
        self.assertEqual(self.rows(path, 'echo')[0], {'when': 'later', 'gap': '300'})

    def test_real_speed_and_silence_are_on_file(self):
        path = self.journalled()
        archers = self.shooter('archers')
        foe = self.enemy('swords', 0, 100)
        self.go('hold')
        self.run_ticks(1, walk=False)
        foe.z = 103
        self.run_ticks(int(self.g.TUNE.silent_limit / 1000) + 2, walk=False)
        self.assertEqual(self.rows(path, 'silent')[0], {'unit': str(archers.id), 'key': 'archers', 'target': str(foe.id)})
        self.assertEqual(self.rows(path, 'unit')[1]['pace'], '0.0')

    def test_casts_and_held_fire_are_on_file(self):
        path = self.journalled()
        self.player('wizard', 0, -80, abilities=self.lua.table_from(['bolt']))
        archers = self.shooter('guns', x=200, z=0, behaviours={'fire_at_will': True})
        self.player('swords', 200, 100, fighting=True)
        self.enemy('lord', 200, 102, fighting=True)
        self.go('hold')
        self.run_ticks(1, walk=False)
        self.assertEqual(self.rows(path, 'cast')[0]['ability'], 'bolt')
        self.assertEqual(self.rows(path, 'fire')[0], {'unit': str(archers.id), 'key': 'guns', 'hold': 'true'})

    def test_the_end_of_a_battle_is_written_once(self):
        path = self.journalled()
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
        self.general.victory()
        self.general.stop()
        self.general.stop()
        self.assertEqual(self.rows(path, 'end'), [{'orders': '1', 'casts': '0'}])
        self.assertEqual(len(self.rows(path, 'victory')), 1)
        self.assertEqual(len(self.rows(path, 'field')), 2)

    def test_a_folder_it_cannot_write_to_costs_nothing(self):
        path = self.journalled()
        self.g.journal_path = str(path.parent / 'missing' / 'general.log')
        self.general = self.lua.execute('return NEW_GENERAL(bm, {data=DATA, journal_file=journal_path})')
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(5, walk=False)
        self.assertEqual(self.general.mode(), 'all')
        self.assertIsNotNone(self.order(unit))

    def test_a_broken_journal_never_stands_the_commander_down(self):
        self.journalled()
        self.lua.execute("io.open = function() error('disk on fire') end")
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(5, walk=False)
        self.assertEqual(self.general.mode(), 'all')
        self.assertIsNotNone(self.order(unit))

    def test_a_failed_picture_never_costs_a_tick(self):
        path = self.journalled()
        unit = self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        unit.number_of_enemies_killed = None
        self.go()
        self.run_ticks(5, walk=False)
        self.assertEqual(self.general.mode(), 'all')
        self.assertEqual(len(self.rows(path, 'order')), 1)
        self.assertEqual([n for n in self.rows(path, 'note') if 'failed' in n['text']], [])

    def test_battles_add_up_in_one_file(self):
        path = self.journalled()
        path.write_text('0\told\n', encoding='utf-8')
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(1, walk=False)
        self.assertEqual(path.read_text(encoding='utf-8').splitlines()[0], '0\told')

    def test_an_oversized_file_is_started_over_once(self):
        path = self.journalled()
        self.g.JOURNAL.limit = 5
        path.write_text('0\told battle\n', encoding='utf-8')
        self.player('swords', 0, 0)
        self.enemy('swords', 0, 50)
        self.go()
        self.run_ticks(3, walk=False)
        text = path.read_text(encoding='utf-8')
        self.assertNotIn('old battle', text)
        self.assertEqual(len(self.rows(path, 'battle')), 1)
        self.assertGreaterEqual(len(self.rows(path, 'order')), 1)

    def test_every_event_stays_on_one_line(self):
        path = self.journalled()
        journal = self.g.NEW_JOURNAL(str(path))
        journal.add(5, 'note', 'text', 'two\nlines\twith a tab', 'gone', None, 'kept', False)
        journal.save()
        self.assertEqual(path.read_text(encoding='utf-8'), '5\tnote\ttext=two lines with a tab\tkept=false\n')

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
        self.g.attacker_side = True
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

    def test_cannons_shoot_flat_and_spears_can_brace(self):
        self.assertTrue(self.units['wh_main_emp_art_great_cannon'][0] & FLAGS['direct'])
        self.assertFalse(self.units['wh_main_emp_art_mortar'][0] & FLAGS['direct'])
        self.assertTrue(self.units['wh_main_emp_inf_handgunners'][0] & FLAGS['direct'])
        self.assertTrue(self.units['wh_main_emp_inf_spearmen_0'][0] & FLAGS['brace'])

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
        self.assertTrue(halberds[0] & FLAGS['anti_large'] and halberds[0] & FLAGS['brace'])
        self.assertEqual(halberds[3], 16)          # bonus v large
        self.assertEqual(halberds[6:8], [26, 42])  # attack, defence
        self.assertEqual(halberds[10:15], [8, 20, 5.6, 1, 70])     # blow, how often, men hit, morale
        franz = self.units['wh_main_emp_cha_karl_franz_0']
        self.assertEqual(franz[9], 4280)
        self.assertEqual(franz[10:14], [140, 330, 4.0, 5])
        reiksguard = self.units['wh_main_emp_cav_reiksguard']
        self.assertTrue(reiksguard[0] & FLAGS['large'] and reiksguard[0] & FLAGS['shock'])
        self.assertEqual(self.units['wh_main_dwf_inf_ironbreakers'][2], 125)
        self.assertTrue(self.units['wh_main_emp_art_mortar'][0] & FLAGS['arty'])

    def test_newest_dlc_is_covered_without_hand_written_lists(self):
        self.assertTrue(any('_dlc29_' in key for key in self.units))
