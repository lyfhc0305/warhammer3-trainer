-- Category tabs in the native elite recruitment window, whose single row of cards
-- gets long once foreign regiments join it. The window and its cards belong to the
-- game and are rebuilt at will, so nothing here keeps a UIComponent between calls:
-- pcall cannot catch a stale C++ pointer.
local function create_elite_tabs(env)
    local MARK, HOLDER = env.prefix .. 'filter_hidden', env.prefix .. 'elite_tabs'
    -- The game's own names run to seven glyphs and nine tabs share one row.
    local SHORT = {
        infantry='步兵', missile_infantry='远程步兵', cavalry_chariots='骑兵战车',
        missile_cavalry_chariots='远程骑兵', monster_beasts='怪兽野兽', flying_war_machine='飞行器械',
        missile_monster_beasts='远程怪兽', artillery_war_machines='炮兵器械',
    }
    local api = {}
    local defs, selected, drawn = {}, 'all', nil
    for _, def in ipairs(env.units) do
        defs[def.id] = def
        defs[def.native_key] = def
    end

    local function child(parent, name)
        local found = parent:Find(name, false)
        return found and UIComponent(found) or nil
    end

    local function category_of(card)
        local def = defs[card.key]
        return def and def.category or 'other'
    end

    -- Cards hidden here are marked ON the card, so one the game hid stays hidden.
    function api.reveal(cards)
        for _, card in ipairs(cards) do
            local marker = child(card.node, MARK)
            if marker and marker:GetStateText() == 'hidden' then
                marker:SetStateText('')
                card.node:SetVisible(true)
            end
        end
    end

    local function tabs_for(cards)
        local tabs, seen, known = {}, {}, 0
        for _, card in ipairs(cards) do
            local id, def = category_of(card), defs[card.key]
            if not seen[id] then
                seen[id] = {id=id, count=0, order=def and def.category_order or 999,
                    label=def and (SHORT[id] or def.category_label) or '其他',
                    title=def and def.category_label or '其他'}
                tabs[#tabs + 1] = seen[id]
                if def then known = known + 1 end
            end
            seen[id].count = seen[id].count + 1
        end
        table.sort(tabs, function(a, b) return a.order < b.order end)
        return tabs, seen, known
    end

    -- The row sits in the free band between the window title and the frame of the list.
    local function draw(list, view, tabs, total)
        local x, y = view:Position()
        local width = view:Dimensions()
        local gap, height = env.px(4), env.px(24)
        local rows = {{id='all', label='全部', title='全部精英', count=total}}
        for _, tab in ipairs(tabs) do rows[#rows + 1] = tab end
        local tab_w = math.min(env.px(96), math.floor((width - gap * (#rows + 1)) / #rows))
        local left = x + math.floor((width - tab_w * #rows - gap * (#rows - 1)) / 2)
        local top = y - height - gap
        local signature = {selected, width, left, top}
        for _, row in ipairs(rows) do signature[#signature + 1] = row.id .. row.count end
        signature = table.concat(signature, ',')
        local holder = child(list, HOLDER)
        if holder and drawn == signature then return end
        if holder then holder:Destroy() end
        drawn = signature
        holder = env.make(list, 'elite_tabs', 'ui/wh3pt/label', nil, left, top, width, height)
        for index, row in ipairs(rows) do
            local tab = env.make(holder, 'elite_tab_' .. row.id, 'ui/wh3pt/button', 'button',
                left + (index - 1) * (tab_w + gap), top, tab_w, height, row.label,
                row.id == selected and 'selected' or 'active')
            tab:SetTooltipText(row.title .. ' · ' .. row.count .. ' 支', true)
        end
    end

    local function clear(list)
        selected, drawn = 'all', nil
        local holder = list and child(list, HOLDER)
        if holder then holder:Destroy() end
    end

    -- Runs after the duplicates are gone, so a tab counts what can really be recruited.
    function api.apply(list, view, boxes, cards, enabled)
        local showing = {}
        for _, card in ipairs(cards) do
            if card.node:Visible(false) then showing[#showing + 1] = card end
        end
        local tabs, seen, known = tabs_for(showing)
        -- Raise dead and mercenaries share this window; they have nothing to sort.
        if enabled and list and view and known >= 2 then
            if not seen[selected] then selected = 'all' end
            draw(list, view, tabs, #showing)
            for _, card in ipairs(showing) do
                if selected ~= 'all' and category_of(card) ~= selected then
                    local marker = child(card.node, MARK)
                    if not marker then
                        marker = UIComponent(card.node:CreateComponent(MARK, 'ui/wh3pt/label'))
                        marker:SetVisible(false)
                    end
                    marker:SetStateText('hidden')
                    card.node:SetVisible(false)
                end
            end
        else
            clear(list)
        end
        for _, box in ipairs(boxes) do
            if box.Layout then pcall(function() box:Layout() end) end
        end
    end

    function api.select(id) selected = id end
    function api.selected() return selected end
    return api
end
