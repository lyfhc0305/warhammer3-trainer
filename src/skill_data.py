"""What automatic skill allocation needs to know about the skill trees.

Read from the installed game at build time. A skill that locks another node of the same
tree is a choice between options, such as mounts, lores and god blessings, so it is left
to the player. CA's utilization hints name the skills its own AI finds useless; those
come last.
"""
from collections import defaultdict
from pathlib import Path

from database import decode
from pack_io import read_pack

SOURCE_TABLES = frozenset({
    'character_skill_nodes_tables', 'character_skill_node_set_items_tables',
    'character_skill_node_sets_tables', 'character_skill_nodes_skill_locks_tables',
    'character_skill_utilization_hints_junctions_tables',
})


def skill_data(game):
    db = read_pack(Path(game) / 'data/db.pack', lambda n: n.startswith('db\\') and n.split('\\')[1] in SOURCE_TABLES)

    def rows(table):
        found = [r for n, d in db.items() if n.split('\\')[1] == table for r in decode(table, d)[1]]
        if not found:
            raise ValueError('游戏缺少技能表：' + table)
        return found

    node_skill = {r['key']: r['character_skill_key'] for r in rows('character_skill_nodes_tables')}
    set_nodes = defaultdict(set)
    for r in rows('character_skill_node_set_items_tables'):
        set_nodes[r['set']].add(r['item'])
    lockers = defaultdict(set)
    for r in rows('character_skill_nodes_skill_locks_tables'):
        lockers[r['character_skill_node']].add(r['character_skill'])
    # Keyed by agent subtype, the one thing the campaign script can read off a character.
    # The few subtypes with faction-specific trees get the union of their trees.
    exclusive = defaultdict(set)
    for r in rows('character_skill_node_sets_tables'):
        nodes = set_nodes.get(r['key'], set())
        skills = {node_skill.get(n) for n in nodes}
        for node in nodes:
            for skill in lockers.get(node, ()):
                if skill in skills and r['agent_subtype_key']:
                    exclusive[r['agent_subtype_key']].add(skill)
    useless = {r['character_skill'] for r in rows('character_skill_utilization_hints_junctions_tables')
               if r['hint'] == 'USELESS_ALWAYS'}
    return {
        'complete': True,
        'exclusive': {subtype: {skill: True for skill in sorted(found)} for subtype, found in sorted(exclusive.items())},
        'useless': {skill: True for skill in sorted(useless)},
    }
