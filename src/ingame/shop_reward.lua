-- Kislev prayer rewards. Keep native ritual costs, cooldowns and payloads intact.
-- Native API examples: wh3_narrative_khorne.lua (RitualStartedEvent) and
-- wh2_dlc12_tehenhauin.lua (add_ancillary_to_faction with performing_faction).
local rewards = {
    wh3_main_ritual_ksl_winter_dazh = 'wh2_dlc15_anc_enchanted_item_star_dragon_special1',
    wh3_main_ritual_ksl_winter_dazh_upgraded = 'wh2_dlc15_anc_enchanted_item_star_dragon_special1',
    wh3_main_ritual_ksl_winter_salyak = 'wh2_main_anc_armour_war_crown_of_saphery1',
    wh3_main_ritual_ksl_winter_salyak_upgraded = 'wh2_main_anc_armour_war_crown_of_saphery1',
    wh3_main_ritual_ksl_winter_tor = 'wh_main_anc_talisman_the_eye_of_sheerian1',
    wh3_main_ritual_ksl_winter_tor_upgraded = 'wh_main_anc_talisman_the_eye_of_sheerian1',
    wh3_main_ritual_ksl_winter_ursun = 'wh2_dlc10_anc_weapon_the_widowmaker_31',
    wh3_main_ritual_ksl_winter_ursun_upgraded = 'wh2_dlc10_anc_weapon_the_widowmaker_31',
}
-- Current Kislev uses devotion/public order in an army's province, with
-- separate force/province invocations. Retain the original keys for old saves.
for _, god in ipairs({'dazh', 'salyak', 'tor', 'ursun'}) do
    local key = 'wh3_main_ritual_ksl_winter_' .. god
    rewards[key .. '_force'] = rewards[key]
    rewards[key .. '_province'] = rewards[key]
end

core:add_listener(
    'WH3PT_KislevPrayerReward',
    'RitualStartedEvent',
    function(context)
        local faction = context:performing_faction()
        return faction:is_human() and faction:subculture() == 'wh3_main_sc_ksl_kislev'
            and rewards[context:ritual():ritual_key()] ~= nil
    end,
    function(context)
        local faction = context:performing_faction()
        local key = context:ritual():ritual_key()
        cm:add_ancillary_to_faction(faction, rewards[key], false)
        local logger = type(ModLog) == 'function' and ModLog or out
        logger('[WH3PT-SHOP] reward_requested faction=' .. faction:name()
            .. ' ritual=' .. key .. ' item=' .. rewards[key])
    end,
    true
)
