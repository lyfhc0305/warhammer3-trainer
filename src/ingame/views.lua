-- What the campaign panel shows and how clicks move through it. The model
-- (effects, commands, read-back) stays in panel.lua and is reached through ctx;
-- nothing here touches game state directly except saved UI preferences.
local function create_views(ctx)
    local K, M = ctx.kit, ctx.model
    local PREFIX, PAGES, COMMANDS, STRATEGIC = ctx.prefix, ctx.pages, ctx.commands, ctx.strategic
    local definitions, command_defs, draft, cmd_draft, favourites = ctx.definitions, ctx.command_defs, ctx.draft, ctx.cmd_draft, ctx.favourites
    local px, label, card, button, input, refit = K.px, K.label, K.card, K.button, K.input, K.refit
    local substring = string.sub_lua or string.sub
    local literal_find = string.find_lua or string.find
    local CX, CW, ROW_W, RAIL_W, DESIGN_W, FRAME_INSET = K.CX, K.CW, K.ROW_W, K.RAIL_W, K.DESIGN_W, K.FRAME_INSET
    local PAGE_Y, LIST_Y, ROW_H, ROW_GAP, SECTION_H, CHIP_H = 62, 102, 52, 4, 30, 28
    local ELITE_LIST_Y, ELITE_ROW_H = 154, 64
    local V = {}
    -- UI state. Model state (busy, selection, native pool) lives in M.
    local ui = {
        group = '总览', query = '', search_text = '', search_open = false, only_favourites = false,
        visible = false, collapsed = false, sheet = false, overlay = nil,
        strategic_sort = 'recruitment_cost', strategic_category = 'all', own_regiments = true,
        home_resource_page = 1, armed = nil, undo = nil,
    }
    V.ui = ui
    local panel, launcher, rows_parent, overlay_parent, title_ui, rail_count_ui, status_ui, status_dot_ui, list_ui, search
    local current_rows, strategic_inputs, strategic_draft = {}, {}, {}
    local gen, timer_seq = 0, 0
    local homes = {}

    -- Real-time timers for countdowns: campaign time pauses in dialogs.
    local function later(ms, fn)
        timer_seq = timer_seq + 1
        local name = PREFIX .. 'ui_timer_' .. timer_seq
        if cm.real_callback then cm:real_callback(fn, ms, name) else cm:callback(fn, ms / 1000) end
    end
    V.later = later

    local function group_digits(n)
        local s = tostring(math.floor((tonumber(n) or 0) + 0.5))
        local neg = substring(s, 1, 1) == '-'
        if neg then s = substring(s, 2) end
        local tail = ''
        while #s > 3 do
            tail = ',' .. substring(s, #s - 2) .. tail
            s = substring(s, 1, #s - 3)
        end
        return (neg and '-' or '') .. s .. tail
    end

    local function row_detail(def, ok)
        if not ok then return '读取失败，请重新打开面板' end
        if def.hint and def.hint ~= '' then return def.hint end
        return '范围 ' .. tostring(def.min or 0) .. '～' .. tostring(def.max) .. def.unit
    end

    local function section(parent, id, dy, title, right_text, width)
        width = width or CW
        label(parent, 'sec_' .. id, CX, dy, width - 130, 24, title, 'section')
        if right_text then label(parent, 'secr_' .. id, CX + width - 130, dy, 130, 24, right_text, 'quiet_right') end
        card(parent, 'div_' .. id, CX, dy + 25, width, 1, '', 'divider')
    end

    local function active_row(parent, prefix, it, x, w, ry)
        local id = it.def.id
        local split = math.floor(w * 0.5)
        card(parent, prefix .. '_row_' .. id, x, ry, w, 32, '', 'active')
        label(parent, prefix .. '_name_' .. id, x + 12, ry + 4, split - 12, 24, it.def.label, 'body')
        label(parent, prefix .. '_val_' .. id, x + split, ry + 4, w - split - 48, 24, it.text, 'accent_right')
        button(parent, 'off_' .. id, x + w - 34, ry + 3, 30, 26, '×', 'ghost_card')
    end

    local function selected_name()
        local ok, name = pcall(function()
            local ch = cm:get_character_by_cqi(M.selected_cqi)
            local surname = ch:get_surname()
            return common.get_localised_string(ch:get_forename())
                .. (surname ~= '' and (' ' .. common.get_localised_string(surname)) or '')
        end)
        return ok and name ~= '' and name or ('#' .. tostring(M.selected_cqi))
    end

    -- Search bar: typing has no engine event, so a button runs the query.
    local function draw_search(parent, with_favourites)
        local extra = with_favourites and 36 or 0
        local sw = CW - 56 - 6 - 32 - 6 - extra
        search = input(parent, 'search', CX, 60, sw, 32, ui.search_text)
        button(parent, 'search_go', CX + sw + 6, 60, 56, 32, '搜索', 'accent')
        if with_favourites then
            button(parent, 'favourites_only', CX + CW - 32 - extra, 62, 30, 28, ui.only_favourites and '★' or '☆',
                ui.only_favourites and 'chip_on' or 'chip')
        end
        button(parent, 'search_clear', CX + CW - 32, 60, 32, 32, '×', 'active')
    end

    -- One strip of context above a list page: what the page acts on right now,
    -- with the favourites filter and search on its right.
    local function draw_strip(parent)
        if ui.search_open then return draw_search(parent, true) end
        local main, sub, lit = '', '', false
        local ok_f, f = pcall(ctx.faction)
        if ui.only_favourites then
            main, sub = '只看收藏', '点 ☆ 取消筛选；收藏的功能也显示在总览的常用区'
        elseif ui.group == '军队' then
            local count = tostring(#(ok_f and ctx.collect_characters(f, false) or {}))
            lit = M.selected_cqi ~= nil
            main = (lit and ('已选中 ' .. selected_name()) or '未选中角色') .. ' · 在役将领 ' .. count .. ' 名'
            local targets = ctx.targets()
            sub = targets and targets.summary() or '请先选中我方军队'
        elseif ui.group == '内政' then
            main = '国库 ' .. ((ok_f and f.treasury) and (group_digits(f:treasury()) .. ' 金') or '—')
            local ok_i, income = pcall(function() return f:income() end)
            sub = ok_i and type(income) == 'number' and ('预计每回合收入 ' .. group_digits(income) .. ' 金') or '预计收入以顶栏为准'
        else
            main, sub = '全部只在手动战斗生效', '战役地图上只保存设置；不影响战前预测和自动结算'
        end
        card(parent, 'strip', CX, 60, CW, 38, '', lit and 'accent' or 'active')
        label(parent, 'strip_main', CX + 14, 62, CW - 28 - 92, 18, main, lit and 'accent' or 'muted')
        label(parent, 'strip_sub', CX + 14, 80, CW - 28 - 92, 16, sub, 'hint')
        button(parent, 'favourites_only', CX + CW - 88, 65, 30, 28, ui.only_favourites and '★' or '☆',
            ui.only_favourites and 'chip_on' or 'chip')
        button(parent, 'search_open', CX + CW - 52, 65, 44, 28, '搜索', 'chip')
    end

    -- ------------------------------------------------------------------
    -- Native scroll list. ui/wh3pt/list is the engine's Listview: it clips,
    -- scrolls and owns the wheel; the panel only fills it. The key names what
    -- the list shows, not its rows: the list survives renders while the key is
    -- unchanged, which keeps the scroll position. Within it a row is rebuilt
    -- only when its signature changes, moves when rows above it change height,
    -- and goes when a render no longer draws it.
    -- ------------------------------------------------------------------
    local function close_list()
        if list_ui then list_ui.view:Destroy(); list_ui = nil end
    end

    local function child_of(parent, name)
        local found = parent:Find(name, false)
        return found and UIComponent(found) or nil
    end

    local function fit_part(u, w, h)
        u:SetCanResizeWidth(true); u:SetCanResizeHeight(true); u:Resize(w, h)
    end

    local function open_list(key, content_h, top, bottom, step)
        top, bottom = top or LIST_Y, bottom or K.CONTENT_BOTTOM
        content_h = math.max(1, content_h)
        local x0, y0 = px(CX), px(top)
        local row_w = px(CX + ROW_W) - x0
        if list_ui and list_ui.key == key then
            if list_ui.content_h ~= content_h then
                fit_part(list_ui.box, row_w, px(content_h))
                list_ui.content:Resize(row_w, px(content_h))
                if list_ui.box.Layout then pcall(function() list_ui.box:Layout() end) end
                list_ui.content_h = content_h
            end
            return
        end
        close_list()
        local ox, oy = K.origin()
        local w, h = px(CX + CW) - x0, px(bottom) - y0
        local view = K.make(panel, 'list', 'ui/wh3pt/list', nil, ox + x0, oy + y0, w, h)
        local clip = assert(child_of(view, 'list_clip'), '缺少列表裁剪区')
        local box = assert(child_of(clip, 'list_box'), '缺少列表容器')
        fit_part(clip, row_w, h)
        fit_part(box, row_w, px(content_h))
        local slider = child_of(view, 'vslider')
        if slider then
            local arrow = child_of(slider, 'top')
            local _, arrow_h = (arrow or slider):Dimensions()
            local slider_w = slider:Dimensions()
            fit_part(slider, slider_w, h - 2 * (arrow and arrow_h or px(24)))
            pcall(function() slider:SetProperty('step_size', tostring(px(step or ROW_H + ROW_GAP))) end)
        end
        local content = K.make(box, 'list_content', 'ui/wh3pt/label', nil, ox + x0, oy + y0, row_w, px(content_h), '', 'active')
        if box.Layout then pcall(function() box:Layout() end) end
        view:PropagatePriority(200)
        list_ui = {key = key, view = view, box = box, content = content, content_h = content_h, top = top, rows = {}}
    end

    -- Places one list row at design position y: an unchanged row only moves there.
    local function list_row(id, y, h, signature, draw)
        local entry = list_ui.rows[id]
        if not entry or entry.signature ~= signature then
            if entry then entry.holder:Destroy() end
            local holder = label(list_ui.content, 'row_' .. id, CX, y, ROW_W, h, '')
            entry = {signature = signature, holder = holder, y = y, row = draw(holder)}
            list_ui.rows[id] = entry
        elseif entry.y ~= y then
            local ox, oy = K.origin()
            entry.holder:MoveTo(ox + px(CX), oy + px(y))
            entry.y = y
        end
        list_ui.drawn[id] = true
        return entry.row
    end

    -- Runs fn with the design origin moved so that the list's top maps to the
    -- top of the scrolled content; rows then use the same coordinates as everything
    -- else. Rows fn did not draw have left the list.
    local function in_list(fn)
        local cx, cy = list_ui.content:Position()
        local saved_x, saved_y = K.origin()
        K.set_origin(cx - px(CX), cy - px(list_ui.top))
        list_ui.drawn = {}
        local ok, err = pcall(fn)
        K.set_origin(saved_x, saved_y)
        if not ok then error(err, 0) end
        for id, entry in pairs(list_ui.rows) do
            if not list_ui.drawn[id] then
                entry.holder:Destroy()
                list_ui.rows[id] = nil
            end
        end
    end

    -- ------------------------------------------------------------------
    -- Rows. The state button only shows and flips the state; a changed value
    -- on a live row grows the row by a line with its own 应用 / 还原 chips.
    -- ------------------------------------------------------------------
    local function row_state(def)
        local ok, active, actual = pcall(ctx.read, def)
        local lit = ok and active
        local changed = lit and not def.toggle and tonumber(draft[def.id]) ~= actual
        return ok, lit, actual, changed
    end

    local function row_height(def, changed)
        return ROW_H + (def.presets and 32 or 0) + (changed and CHIP_H or 0)
    end

    local function row_head(parent, def, ry, width, lit, detail)
        local starred = favourites[def.id]
        button(parent, 'fav_' .. def.id, CX + 6, ry + 13, 26, 26, starred and '★' or '☆',
            starred and (lit and 'star_lit' or 'star') or (lit and 'ghost_lit' or 'ghost_card'))
        label(parent, 'name_' .. def.id, CX + 36, ry + 4, width, 24, def.short or def.label, lit and 'accent' or 'active')
        label(parent, 'info_' .. def.id, CX + 36, ry + 27, width, 20, detail, 'hint')
    end

    local function draw_row(parent, def, ry)
        local ok, lit, actual, changed = row_state(def)
        local row = {def = def}
        local height = row_height(def, changed)
        card(parent, 'card_' .. def.id, CX, ry, ROW_W, height, '', lit and 'enabled' or 'active')
        local right = CX + ROW_W - 8
        button(parent, 'toggle_' .. def.id, right - 56, ry + 13, 56, 26,
            not ok and '异常' or lit and '● 开' or '○ 关',
            not ok and 'inactive' or lit and 'enabled' or 'active'):SetDisabled(not ok or M.busy)
        if def.toggle then
            row_head(parent, def, ry, ROW_W - 36 - 72, lit, row_detail(def, ok))
            return row
        end
        row_head(parent, def, ry, ROW_W - 36 - 198, lit, row_detail(def, ok))
        button(parent, 'minus_' .. def.id, right - 182, ry + 13, 26, 26, '−', 'active')
        if def.choices then
            local idx = tonumber(draft[def.id]) or def.default
            button(parent, 'choice_' .. def.id, right - 154, ry + 13, 64, 26, tostring(def.choices[idx] or '?') .. def.unit, 'selected')
        else
            row.input = input(parent, 'input_' .. def.id, right - 154, ry + 13, 64, 26, draft[def.id])
        end
        button(parent, 'plus_' .. def.id, right - 88, ry + 13, 26, 26, '+', 'active')
        local line = ry + ROW_H - 2
        if def.presets then
            local pw = (ROW_W - 36 - 8 - 18) / 4
            for i, v in ipairs(def.presets) do
                button(parent, 'incomequick_' .. tostring(v), CX + 36 + (i - 1) * (pw + 6), line,
                    pw, 26, '+' .. tostring(v) .. '%', 'active'):SetDisabled(not ok or M.busy)
            end
            line = line + 32
        end
        if changed then
            local shown = def.choices and tostring(def.choices[actual] or actual) or tostring(actual)
            label(parent, 'pending_' .. def.id, CX + 36, line, ROW_W - 36 - 160, 24,
                '当前 ' .. shown .. def.unit .. ' · 未应用', 'hint')
            local target = def.choices and tostring(def.choices[tonumber(draft[def.id])] or '?') or tostring(draft[def.id])
            button(parent, 'apply_' .. def.id, right - 148, line, 90, 24, '应用 ' .. target .. def.unit, 'accent'):SetDisabled(M.busy)
            button(parent, 'revert_' .. def.id, right - 54, line, 54, 24, '还原', 'ghost_lit')
        end
        return row
    end

    local function draw_command_row(parent, cmd, ry)
        local row = {def = cmd, is_cmd = true}
        card(parent, 'card_cmd_' .. cmd.id, CX, ry, ROW_W, ROW_H, '', 'active')
        local right = CX + ROW_W - 8
        local text, disabled, state = cmd.btn, M.busy, 'accent'
        local targets, mortarchs = ctx.targets(), ctx.mortarchs()
        if cmd.id == 'free_skills' then
            local on = targets and targets.enabled()
            text, state = on and '● 开' or '○ 关', on and 'enabled' or 'active'
        elseif cmd.id == 'mortarchs' then
            local on = mortarchs and mortarchs.enabled()
            text, state = on and '● 开' or '○ 关', on and 'enabled' or 'active'
            disabled = M.busy or not (mortarchs and mortarchs.supported())
        elseif cmd.id == 'mortarch_buildings' then
            disabled = M.busy or not (mortarchs and mortarchs.buildings_supported())
        elseif cmd.id == 'auto_upgrade' then
            local upgrades = ctx.auto_upgrade()
            local on = upgrades and upgrades.enabled()
            text, state = on and '● 开' or '○ 关', on and 'enabled' or 'active'
        elseif cmd.id == 'auto_skills' then
            local skills = ctx.auto_skills()
            local on = skills and skills.enabled()
            text, state = on and '● 开' or '○ 关', on and 'enabled' or 'active'
        end
        button(parent, 'cmd_' .. cmd.id, right - 56, ry + 13, 56, 26, text, state):SetDisabled(disabled)
        if cmd.toggle then
            row_head(parent, cmd, ry, ROW_W - 36 - 72, false, cmd.hint)
            return row
        end
        row_head(parent, cmd, ry, ROW_W - 36 - 198, false, cmd.hint)
        button(parent, 'cminus_' .. cmd.id, right - 182, ry + 13, 26, 26, '−', 'active')
        row.input = input(parent, 'cinput_' .. cmd.id, right - 154, ry + 13, 64, 26, cmd_draft[cmd.id])
        button(parent, 'cplus_' .. cmd.id, right - 88, ry + 13, 26, 26, '+', 'active')
        return row
    end

    local function draw_empty_state(parent, title, subtitle, action_id, action_label)
        local ey = LIST_Y + 16
        card(parent, 'empty_card', CX, ey, CW, 168, '', 'active')
        label(parent, 'empty_t1', CX + 16, ey + 30, CW - 32, 30, title, 'title_center')
        label(parent, 'empty_t2', CX + 16, ey + 66, CW - 32, 22, subtitle, 'quiet_center')
        if action_id then
            button(parent, action_id, CX + (CW - 148) / 2, ey + 108, 148, 34, action_label, 'accent')
        end
    end

    local function command_signature(def)
        local targets, mortarchs, upgrades, skills = ctx.targets(), ctx.mortarchs(), ctx.auto_upgrade(), ctx.auto_skills()
        return table.concat({tostring(cmd_draft[def.id]), tostring(favourites[def.id]), tostring(M.busy),
            tostring(def.id == 'free_skills' and targets and targets.enabled()),
            tostring(def.id == 'mortarchs' and mortarchs and mortarchs.enabled()),
            tostring(def.id == 'auto_upgrade' and upgrades and upgrades.enabled()),
            tostring(def.id == 'auto_skills' and skills and skills.enabled())}, '|')
    end

    local function feature_signature(def)
        local ok, lit, actual, changed = row_state(def)
        return table.concat({tostring(ok), tostring(lit), tostring(actual), tostring(changed),
            tostring(favourites[def.id]), tostring(draft[def.id]), tostring(M.busy)}, '|'), changed
    end

    local function mortarch_hidden(def)
        local mortarchs = ctx.mortarchs()
        return (def.id == 'mortarchs' or def.id == 'mortarch_buildings') and not (mortarchs and mortarchs.supported())
    end

    -- Appends one feature or command row entry; returns the height it takes.
    local function entry_for(def)
        if command_defs[def.id] then
            return {def = def, h = ROW_H, signature = command_signature(def), draw = draw_command_row}
        end
        local signature, changed = feature_signature(def)
        return {def = def, h = row_height(def, changed), signature = signature, draw = draw_row}
    end

    local function matches(def)
        if ui.only_favourites and not favourites[def.id] then return false end
        if ui.query == '' then return true end
        return literal_find(def.short or def.label, ui.query, 1, true) or literal_find(def.id, ui.query, 1, true)
    end

    -- ------------------------------------------------------------------
    -- Catalog pages: sections of the current page, or every page when the
    -- favourites filter or a search is on.
    -- ------------------------------------------------------------------
    local function draw_catalog()
        local entries, total = {}, 0
        local cross = ui.query ~= '' or ui.only_favourites
        for _, page in ipairs(PAGES) do
            for index, block in ipairs((cross or ui.group == page.name) and page.sections or {}) do
                local title = {title = (cross and (page.name .. ' · ') or '') .. block[1], id = page.name .. index}
                for i = 2, #block do
                    local def = definitions[block[i]] or command_defs[block[i]]
                    if not mortarch_hidden(def) and matches(def) then
                        if title then
                            entries[#entries + 1] = title
                            total = total + SECTION_H
                            title = nil
                        end
                        local entry = entry_for(def)
                        entries[#entries + 1] = entry
                        total = total + entry.h + ROW_GAP
                    end
                end
            end
        end
        if #entries == 0 then
            close_list()
            if ui.only_favourites and ui.query == '' then
                draw_empty_state(rows_parent, '收藏夹还是空的', '在列表中点 ☆ 即可收藏常用功能', 'clear_filter', '清除筛选')
            else
                draw_empty_state(rows_parent, '未找到 “' .. (ui.query ~= '' and ui.query or '条件') .. '”',
                    '换个关键词，或清除搜索', 'empty_clear', '清除搜索')
            end
            return
        end
        open_list(table.concat({'catalog', ui.group, ui.query, tostring(ui.only_favourites)}, '|'), total - ROW_GAP)
        in_list(function()
            local y = LIST_Y
            for _, entry in ipairs(entries) do
                if entry.title then
                    local top = y
                    list_row('section_' .. entry.id, y, SECTION_H, entry.title, function(holder)
                        label(holder, 'sec_' .. entry.id, CX, top + 4, ROW_W, 20, entry.title, 'section')
                        card(holder, 'div_' .. entry.id, CX, top + 25, ROW_W, 1, '', 'divider')
                    end)
                    y = y + SECTION_H
                else
                    local top, def, draw = y, entry.def, entry.draw
                    current_rows[#current_rows + 1] = list_row(def.id, y, entry.h, entry.signature, function(holder)
                        return draw(holder, def, top)
                    end)
                    y = y + entry.h + ROW_GAP
                end
            end
        end)
    end

    -- ------------------------------------------------------------------
    -- Overview: treasury, special resource, favourites, one-click actions and
    -- what is in effect, as one scrolling list.
    -- ------------------------------------------------------------------
    local function favourite_defs()
        local result = {}
        for _, page in ipairs(PAGES) do
            for _, block in ipairs(page.sections or {}) do
                for i = 2, #block do
                    local def = definitions[block[i]] or command_defs[block[i]]
                    if favourites[def.id] and not mortarch_hidden(def) then result[#result + 1] = def end
                end
            end
        end
        return result
    end

    local function draw_gold_block(holder, y)
        section(holder, 'gold', y, '国库资金', nil, ROW_W)
        local ok_f, f = pcall(ctx.faction)
        local gold = (ok_f and f.treasury) and (group_digits(f:treasury()) .. ' 金') or '—'
        label(holder, 'gold_val', CX, y + 30, ROW_W - 3 * 64 - 3 * 8, 36, gold, 'value')
        for idx, item in ipairs({{10000, '+1万'}, {50000, '+5万'}, {200000, '+20万'}}) do
            local b = button(holder, 'gold_' .. tostring(item[1]), CX + ROW_W - (4 - idx) * 64 - (3 - idx) * 8, y + 30, 64, 36, item[2], 'active')
            b:SetDisabled(M.busy)
        end
        local income_on, income_value = ctx.income.read()
        button(holder, 'income_open', CX + ROW_W - 180, y, 180, 24,
            '收入增幅  ' .. (income_on and ('+' .. tostring(income_value) .. '%') or '未开启') .. '  ›',
            income_on and 'enabled' or 'ghost_page')
    end

    local function draw_resource_block(holder, y, resources)
        local item = resources[ui.home_resource_page]
        local key = 'resource_' .. item.index
        card(holder, 'home_resource_card', CX, y, ROW_W, 64, '', 'active')
        local icon = K.icon(holder, 'home_resource_icon', CX + 8, y + 16, 32, 32)
        icon:SetImagePath(item.def.icon, 0, false)
        icon:SetTooltipText(ctx.strategic_name(item.def) .. '：当前 ' .. item.value ..
            (item.def.kind == 'sigils' and '\n设置剩余可用数量；保留已强化的符印' or ''), true)
        label(holder, 'home_resource_name', CX + 48, y + 2, ROW_W - 140, 24, ctx.strategic_name(item.def), 'body')
        local value = input(holder, 'home_resource_value', CX + 48, y + 30, 88, 26,
            strategic_draft[key] or tostring(item.value))
        label(holder, 'home_resource_current', CX + 142, y + 30, ROW_W - 220, 26, '当前 ' .. item.value, 'quiet')
        button(holder, 'resource_do_' .. item.index, CX + ROW_W - 68, y + 30, 60, 26, '设置', 'accent'):SetDisabled(M.busy)
        if #resources > 1 then
            button(holder, 'resource_prev', CX + ROW_W - 90, y + 2, 24, 24, '‹', 'active')
            label(holder, 'home_resource_pages', CX + ROW_W - 64, y + 2, 38, 24,
                ui.home_resource_page .. '/' .. #resources, 'center')
            button(holder, 'resource_next', CX + ROW_W - 24, y + 2, 24, 24, '›', 'active')
        end
        return {resource_key = key, input = value}
    end

    local function draw_quick_block(holder, y)
        local bw = (ROW_W - 2 * 8) / 3
        for idx, item in ipairs({{'move_all', '补满全部军队'}, {'preset_campaign', #ctx.presets.campaign .. ' 项 · 先预览'},
            {'preset_battle', #ctx.presets.battle .. ' 项 · 先预览'}}) do
            local id, bx = item[1], CX + (idx - 1) * (bw + 8)
            button(holder, 'cmd_' .. id, bx, y, bw, 54, '', 'active'):SetDisabled(M.busy)
            label(holder, 'ptitle_' .. id, bx + 4, y + 6, bw - 8, 24, command_defs[id].short, 'center')
            label(holder, 'psub_' .. id, bx + 4, y + 29, bw - 8, 20, item[2], 'quiet_center')
        end
    end

    local function draw_home(act_list)
        local resources = {}
        for index, def in ipairs(STRATEGIC.resources) do
            local resource = ctx.resource_interface(def)
            if resource then resources[#resources + 1] = {index = index, def = def, value = resource:value()} end
        end
        if #resources > 0 then ui.home_resource_page = math.max(1, math.min(ui.home_resource_page, #resources)) end
        local favs = favourite_defs()
        local shown = math.min(#act_list, 6)
        local blocks = {}
        local function block(id, h, signature, draw) blocks[#blocks + 1] = {id = id, h = h, signature = signature, draw = draw} end
        local ok_f, f = pcall(ctx.faction)
        local treasury = ok_f and f.treasury and f:treasury() or 0
        local income_on, income_value = ctx.income.read()
        block('home_gold', 78, table.concat({tostring(treasury), tostring(income_on), tostring(income_value), tostring(M.busy)}, '|'), draw_gold_block)
        if #resources > 0 then
            local item = resources[ui.home_resource_page]
            block('home_res', 72, table.concat({tostring(ui.home_resource_page), tostring(#resources), tostring(item.value),
                tostring(strategic_draft['resource_' .. item.index]), tostring(M.busy)}, '|'),
                function(holder, y) return draw_resource_block(holder, y, resources) end)
        end
        block('sec_fav', SECTION_H, '常用|' .. #favs, function(holder, y) section(holder, 'fav', y, '常用', #favs > 0 and (#favs .. ' 项') or '点 ☆ 收藏', ROW_W) end)
        if #favs == 0 then
            block('home_fav_empty', 52, 'empty', function(holder, y)
                card(holder, 'fav_empty_card', CX, y, ROW_W, 44, '', 'active')
                label(holder, 'fav_empty_t', CX + 14, y + 10, ROW_W - 28, 24, '在任意列表里点 ☆，常用功能就会出现在这里', 'quiet')
            end)
        end
        for _, def in ipairs(favs) do
            local entry = entry_for(def)
            local draw = entry.draw
            block(def.id, entry.h + ROW_GAP, entry.signature, function(holder, y) return draw(holder, def, y) end)
        end
        block('sec_quick', SECTION_H, 'quick', function(holder, y) section(holder, 'quick', y, '一键操作', nil, ROW_W) end)
        block('home_quick', 66, 'quick|' .. tostring(M.busy), draw_quick_block)
        block('sec_active', SECTION_H, 'active|' .. #act_list, function(holder, y)
            section(holder, 'active', y, '正在生效', tostring(#act_list) .. ' 项', ROW_W)
        end)
        if #act_list == 0 then
            block('home_empty', 70, 'empty', function(holder, y)
                card(holder, 'empty_active', CX, y, ROW_W, 64, '', 'active')
                label(holder, 'empty_t1', CX + 12, y + 6, ROW_W - 24, 24, '暂无生效的修改', 'center')
                label(holder, 'empty_t2', CX + 12, y + 32, ROW_W - 24, 22, '开启任意功能后会列在这里', 'quiet_center')
            end)
        end
        for i = 1, shown do
            local it = act_list[i]
            block('act_' .. it.def.id, 38, it.text .. '|' .. tostring(M.busy), function(holder, y) active_row(holder, 'act', it, CX, ROW_W, y) end)
        end
        if #act_list > shown then
            block('home_more', 40, tostring(#act_list), function(holder, y)
                button(holder, 'sheet_open', CX, y, ROW_W, 32, '查看全部 ' .. tostring(#act_list) .. ' 项  ›', 'ghost_page')
            end)
        end
        local total = 0
        for _, b in ipairs(blocks) do total = total + b.h end
        open_list('home', total, PAGE_Y)
        in_list(function()
            local y = PAGE_Y
            for _, b in ipairs(blocks) do
                local top, draw = y, b.draw
                -- Rows survive renders, so their inputs are registered every time.
                local row = list_row(b.id, y, b.h, b.signature, function(holder) return draw(holder, top) end)
                if row then
                    if row.def and row.input then current_rows[#current_rows + 1] = row end
                    if row.resource_key then strategic_inputs[row.resource_key] = row.input end
                end
                y = y + b.h
            end
        end)
    end

    -- ------------------------------------------------------------------
    -- Sheet: everything in effect, from any page, with the two-step clear.
    -- ------------------------------------------------------------------
    local function draw_active_sheet(act_list)
        button(rows_parent, 'sheet_close', CX, 62, 72, 28, '‹ 返回', 'chip')
        label(rows_parent, 'sheet_count', CX + 80, 62, CW - 80, 28, '共 ' .. tostring(#act_list) .. ' 项', 'quiet_right')
        local bottom = K.CONTENT_BOTTOM - 44
        if ui.armed then
            button(rows_parent, 'clear', CX, bottom + 8, CW, 32, '确认全关 · ' .. tostring(ui.armed.left), 'danger_solid'):SetDisabled(M.busy)
        else
            button(rows_parent, 'clear', CX, bottom + 8, CW, 32, '全部关闭…', 'danger'):SetDisabled(M.busy or #act_list == 0)
        end
        if #act_list == 0 then
            close_list()
            label(rows_parent, 'sheet_empty', CX + 16, LIST_Y + 60, CW - 32, 24, '当前没有生效的修改', 'quiet_center')
            return
        end
        open_list('sheet', #act_list * 38 - 6, LIST_Y, bottom)
        in_list(function()
            for i, it in ipairs(act_list) do
                local y = LIST_Y + (i - 1) * 38
                list_row('sheet_' .. it.def.id, y, 32, it.text .. '|' .. tostring(M.busy), function(holder)
                    active_row(holder, 'sheet', it, CX, ROW_W, y)
                end)
            end
        end)
    end

    -- ------------------------------------------------------------------
    -- Elite recruitment: filters stay put, the cards scroll.
    -- ------------------------------------------------------------------
    local function draw_strategic()
        local parent = rows_parent
        local matches_, f, categories, category_counts, total = {}, ctx.faction(), {}, {}, 0
        if ui.search_open then
            draw_search(parent)
        else
            card(parent, 'strip', CX, 60, CW, 32, '', 'active')
            label(parent, 'strip_main', CX + 14, 64, CW - 28 - 56, 24, '原生招募窗口里招募；重复兵牌自动合并', 'muted')
            button(parent, 'search_open', CX + CW - 50, 62, 44, 28, '搜索', 'chip')
        end
        for index, def in ipairs(STRATEGIC.units) do
            local name, own = ctx.strategic_name(def), ctx.regiment_belongs(def, f)
            if not ui.own_regiments or own then
                local category = def.category or 'other'
                categories[category] = {id = category, label = def.category_label or '其他', order = def.category_order or 999}
                if ui.query == '' or literal_find(name, ui.query, 1, true) or literal_find(def.id, ui.query, 1, true) then
                    total = total + 1
                    category_counts[category] = (category_counts[category] or 0) + 1
                    if ui.strategic_category == 'all' or ui.strategic_category == category then
                        matches_[#matches_ + 1] = {index = index, def = def, name = name, own = own}
                    end
                end
            end
        end
        table.sort(matches_, function(a, b)
            if ui.strategic_sort ~= 'category' then
                local av, bv = a.def[ui.strategic_sort] or 0, b.def[ui.strategic_sort] or 0
                if av ~= bv then return av > bv end
            end
            local ao, bo = a.def.category_order or 999, b.def.category_order or 999
            if ao ~= bo then return ao < bo end
            local ac, bc = a.def.category or 'other', b.def.category or 'other'
            if ac ~= bc then return ac < bc end
            return a.def.id < b.def.id
        end)
        button(parent, 'native_toggle', CX, 98, CW, 26,
            M.native_enabled and '● 原生精英无限招募：已开启' or '○ 开启原生精英无限招募', M.native_enabled and 'enabled' or 'active')
        button(parent, 'strategy_filter', CX, 128, 132, 24, ui.own_regiments and '本阵营精英 ▼' or '全部阵营 ▼', 'chip')
        local category_name = ui.strategic_category == 'all' and '全部类别' or (categories[ui.strategic_category] and categories[ui.strategic_category].label or '其他')
        local sort_labels = {recruitment_cost = '招募费用 ↓', upkeep_cost = '维护费 ↓', category = '原生类别'}
        button(parent, 'strategy_categories', CX + 138, 128, 128, 24, category_name .. (ui.overlay == 'category' and ' ▲' or ' ▼'), 'chip')
        button(parent, 'strategy_sort', CX + 272, 128, CW - 272 - 66, 24, sort_labels[ui.strategic_sort], 'chip')
        label(parent, 'strategy_pages', CX + CW - 62, 128, 62, 24, #matches_ .. ' 项', 'quiet_right')
        if #matches_ == 0 then
            close_list()
            label(parent, 'strategy_empty', CX, ELITE_LIST_Y + 40, CW, 40, '无匹配项目；可清除搜索或切换类别/阵营', 'quiet_center')
        else
            local ids = {}
            for i, item in ipairs(matches_) do ids[i] = tostring(item.index) end
            open_list('elite:' .. table.concat(ids, ','), #matches_ * ELITE_ROW_H - ROW_GAP, ELITE_LIST_Y, nil, ELITE_ROW_H)
            in_list(function()
                for slot, item in ipairs(matches_) do
                    local y = ELITE_LIST_Y + (slot - 1) * ELITE_ROW_H
                    local signature = table.concat({tostring(M.native_enabled), tostring(M.native_selected[item.def.id]), tostring(M.busy)}, '|')
                    list_row('elite_' .. item.index, y, 60, signature, function(holder)
                        card(holder, 'strategy_card_' .. slot, CX, y, ROW_W, 60, '', 'active')
                        local icon = K.icon(holder, 'strategy_icon_' .. slot, CX + 6, y + 4, 28, 56)
                        if item.def.icon and item.def.icon ~= '' then icon:SetImagePath(item.def.icon, 0, false) end
                        local category = item.def.category_label or '其他'
                        local tooltip = item.name .. '\n基础招募费 ' .. tostring(item.def.recruitment_cost or 0) .. ' · 基础维护费 ' .. tostring(item.def.upkeep_cost or 0) .. '\n费用不含领主、科技与阵营修正，并非战斗力评分\n' .. category .. ' · ' .. (item.def.unit_type or '') .. '\n' .. (item.def.details or '')
                        icon:SetTooltipText(tooltip, true)
                        local name_ui = label(holder, 'strategy_name_' .. slot, CX + 42, y + 3, ROW_W - 48, 25, item.name, 'body')
                        name_ui:SetTooltipText(tooltip, true)
                        label(holder, 'strategy_info_' .. slot, CX + 42, y + 31, ROW_W - 162, 26,
                            '招募 ' .. tostring(item.def.recruitment_cost or 0) .. ' · 维护 ' .. tostring(item.def.upkeep_cost or 0), 'quiet')
                        local selected = item.own and M.native_enabled or (not item.own and M.native_selected[item.def.id])
                        button(holder, 'strategy_do_' .. item.index, CX + ROW_W - 112, y + 31, 104, 26,
                            item.own and (M.native_enabled and '关闭解锁' or '开启解锁')
                            or (M.native_selected[item.def.id] and '移出招募池' or '加入招募池'), selected and 'enabled' or 'active')
                    end)
                end
            end)
        end
        if ui.overlay == 'category' then
            local options = {}
            for _, category in pairs(categories) do options[#options + 1] = category end
            table.sort(options, function(a, b) return a.order == b.order and a.id < b.id or a.order < b.order end)
            table.insert(options, 1, {id = 'all', label = '全部类别'})
            local my, mh = 154, math.ceil(#options / 2) * 34 + 12
            overlay_parent = label(panel, 'category_overlay', CX, my, CW, mh, '')
            card(overlay_parent, 'category_background', CX, my, CW, mh, '', 'raise')
            local bw = (CW - 18) / 2
            for i, option in ipairs(options) do
                local count = option.id == 'all' and total or (category_counts[option.id] or 0)
                button(overlay_parent, 'strategycat_' .. option.id,
                    CX + 6 + ((i - 1) % 2) * (bw + 6), my + 6 + math.floor((i - 1) / 2) * 34,
                    bw, 30, option.label .. ' · ' .. count, ui.strategic_category == option.id and 'chip_on' or 'chip')
            end
            overlay_parent:PropagatePriority(230)
        end
    end

    -- ------------------------------------------------------------------
    -- Overlays over the content column: preset preview and scale picker.
    -- ------------------------------------------------------------------
    local function preset_items(kind)
        local items = {}
        for _, pair in ipairs(ctx.presets[kind]) do
            local def = definitions[pair[1]]
            if def then
                local ok, active, actual = pcall(ctx.read, def)
                items[#items + 1] = {def = def, value = pair[2], active = ok and active, actual = actual}
            end
        end
        return items
    end

    local function draw_preset_overlay(kind)
        local title = kind == 'campaign' and '战役发育预设' or '精锐战斗预设'
        local items = preset_items(kind)
        local top, bottom = 60, K.CONTENT_BOTTOM
        overlay_parent = label(panel, 'preset_overlay', CX, top, CW, bottom - top, '')
        card(overlay_parent, 'preset_background', CX, top, CW, bottom - top, '', 'raise')
        local overwritten = 0
        for _, it in ipairs(items) do
            if it.active and (it.def.toggle or math.abs((it.actual or 0) - it.value) > 0.001) then overwritten = overwritten + 1 end
        end
        label(overlay_parent, 'preset_title', CX + 14, top + 8, CW - 28, 28, title .. ' · ' .. #items .. ' 项', 'title')
        label(overlay_parent, 'preset_note', CX + 14, top + 36, CW - 28, 20,
            overwritten > 0 and ('将覆盖已设置的 ' .. overwritten .. ' 项；应用后可撤销') or '部队加成只在手动战斗生效；应用后可撤销', 'hint')
        local y = top + 62
        local row_h = math.min(26, math.floor((bottom - 48 - y) / math.max(1, #items)))
        for i, it in ipairs(items) do
            local shown = it.def.toggle and '开启' or (tostring(it.value) .. it.def.unit)
            local note = ''
            if it.active and not it.def.toggle and math.abs((it.actual or 0) - it.value) > 0.001 then
                note = '覆盖 ' .. tostring(it.actual) .. it.def.unit
            elseif it.active then
                note = '已生效'
            end
            card(overlay_parent, 'preset_row_' .. i, CX + 10, y, CW - 20, row_h - 2, '', it.active and 'enabled' or 'active')
            label(overlay_parent, 'preset_name_' .. i, CX + 20, y, CW * 0.5 - 20, row_h - 2, it.def.short or it.def.label, 'body')
            label(overlay_parent, 'preset_old_' .. i, CX + CW * 0.5, y, CW * 0.5 - 110, row_h - 2, note, 'quiet_right')
            label(overlay_parent, 'preset_val_' .. i, CX + CW - 110, y, 90, row_h - 2, shown, 'accent_right')
            y = y + row_h
        end
        button(overlay_parent, 'preset_apply', CX + CW - 10 - 140, bottom - 40, 140, 32, '应用 ' .. title, 'accent'):SetDisabled(M.busy)
        button(overlay_parent, 'preset_cancel', CX + CW - 10 - 140 - 8 - 72, bottom - 40, 72, 32, '取消', 'active')
        overlay_parent:PropagatePriority(230)
    end

    local function draw_scale_overlay()
        local top, mh = 52, 6 + 2 * 36 + 34
        overlay_parent = label(panel, 'scale_overlay', CX, top, CW, mh, '')
        card(overlay_parent, 'scale_background', CX, top, CW, mh, '', 'raise')
        label(overlay_parent, 'scale_title', CX + 12, top + 6, CW - 24, 24,
            '界面缩放 · 检测到 ' .. tostring(math.floor((K.info.auto or 1) * 100 + 0.5)) .. '%', 'muted')
        local bw = (CW - 12 - 2 * 6) / 3
        for i, step in ipairs(K.UI_SCALE_STEPS) do
            local text = step > 0 and (tostring(math.floor(step * 100 + 0.5)) .. '%') or '自动'
            button(overlay_parent, 'scale_' .. i, CX + 6 + ((i - 1) % 3) * (bw + 6), top + 34 + math.floor((i - 1) / 3) * 36,
                bw, 30, text, math.abs(step - K.pref) < 0.001 and 'chip_on' or 'chip')
        end
        overlay_parent:PropagatePriority(230)
    end

    -- ------------------------------------------------------------------
    -- Render: everything inside the panel below the header, every time.
    -- ------------------------------------------------------------------
    local function render()
        if not panel then return end
        ctx.faction()
        V.capture()
        K.set_origin(panel:Position())   -- the player may have dragged the panel
        if rows_parent then rows_parent:Destroy(); rows_parent = nil end
        if overlay_parent then overlay_parent:Destroy(); overlay_parent = nil end
        current_rows, search = {}, nil
        strategic_inputs = {}
        local act_list = ctx.active_items()
        local total = #act_list

        if rail_count_ui then refit(rail_count_ui, tostring(total) .. ' 项', total > 0 and 'enabled' or 'active') end
        if launcher then
            refit(launcher, ui.visible and '◆ 修改器 [F9]' or ('◆ 修改器 · ' .. tostring(total)),
                ui.visible and 'selected' or (total > 0 and 'enabled' or 'active'))
        end
        for _, item in ipairs(PAGES) do
            local tab_btn = ctx.controls[PREFIX .. 'tab_' .. item.name]
            if tab_btn then refit(tab_btn, item.name, (not ui.collapsed and ui.group == item.name) and 'selected' or 'ghost') end
        end
        if ui.collapsed then return end
        if title_ui then refit(title_ui, ui.sheet and '正在生效' or PAGES[ui.group].title) end
        if status_ui then
            local w = ui.undo and (CW - 16 - 70) or (CW - 16)
            status_ui:Resize(px(w), px(30))
            local m_ok = pcall(function() refit(status_ui, M.status, M.status_error and 'danger' or 'muted') end)
            if not m_ok then status_ui:SetStateText(tostring(M.status)) end
        end
        if status_dot_ui then
            K.set_state(status_dot_ui, M.busy and 'busy_dot' or (M.status_error and 'danger_dot' or 'ok_dot'))
        end

        rows_parent = label(panel, 'rows', CX, 56, CW, K.FOOTER_Y - 4 - 56, '')
        if ui.undo then
            button(rows_parent, 'undo', CX + CW - 62, K.FOOTER_Y + 9, 62, 28, '撤销', 'enabled'):SetDisabled(M.busy)
        end
        if ui.sheet then
            draw_active_sheet(act_list)
        elseif ui.group == '总览' then
            draw_home(act_list)
        elseif ui.group == '精英' then
            draw_strategic()
        else
            draw_strip(rows_parent)
            draw_catalog()
        end
        if ui.overlay == 'preset_campaign' then draw_preset_overlay('campaign')
        elseif ui.overlay == 'preset_battle' then draw_preset_overlay('battle')
        elseif ui.overlay == 'scale' then draw_scale_overlay() end
    end
    V.render = render

    -- Remember typed text before the components holding it are rebuilt.
    function V.capture()
        for key, u in pairs(strategic_inputs) do
            strategic_draft[key] = u:GetStateText()
            local index = tonumber(string.match(key, '^resource_(%d+)$'))
            local def = index and STRATEGIC.resources[index]
            if def then cm:set_saved_value(ctx.setting('resource_' .. def.id, 'target'), strategic_draft[key]) end
        end
        if search then ui.search_text = search:GetStateText() end
        for _, row in ipairs(current_rows) do
            if row.input then
                if row.is_cmd then
                    cmd_draft[row.def.id] = row.input:GetStateText()
                else
                    draft[row.def.id] = row.input:GetStateText()
                end
            end
        end
    end

    function V.show_status()
        if status_ui then
            local ok = pcall(function() refit(status_ui, M.status, M.status_error and 'danger' or 'muted') end)
            if not ok then status_ui:SetStateText(tostring(M.status)) end
        end
        if status_dot_ui then
            K.set_state(status_dot_ui, M.busy and 'busy_dot' or (M.status_error and 'danger_dot' or 'ok_dot'))
        end
    end

    -- A draft the model changed (apply, preset, undo) is pushed into any input
    -- still on screen, so the next capture does not write the old text back.
    function V.sync_input(id)
        for _, row in ipairs(current_rows) do
            if row.def and row.def.id == id and row.input then row.input:SetStateText(tostring(draft[id])) end
        end
    end

    function V.refresh()
        if ui.visible and panel then ctx.guarded(render) end
    end

    -- ------------------------------------------------------------------
    -- Panel construction, placement and dragging.
    -- ------------------------------------------------------------------
    local function launcher_x(rw, lw)
        local ok, left = pcall(function()
            local tabs = type(find_uicomponent) == 'function'
                and find_uicomponent(core:get_ui_root(), 'hud_campaign', 'bar_small_top')
            if tabs then return (tabs:Position()) end
        end)
        if ok and type(left) == 'number' and left > rw * 0.5 then
            return math.max(0, left - px(K.LAUNCHER_CLEARANCE) - lw)
        end
        return math.max(0, rw - K.EDGE_RIGHT - lw - math.floor(rw * 0.16))
    end

    local function store_point(u, name)
        local x, y = u:Position()
        cm:set_saved_value(PREFIX .. name .. '_x', x)
        cm:set_saved_value(PREFIX .. name .. '_y', y)
    end

    local function left_home(u, name)
        local x, y = u:Position()
        local home = homes[name]
        if not home or math.abs(x - home[1]) + math.abs(y - home[2]) <= K.DRAG_SLOP then return false end
        homes[name] = {x, y}
        store_point(u, name)
        return true
    end

    local function place(u, name, default_x, default_y, max_x, max_y)
        local x, y = cm:get_saved_value(PREFIX .. name .. '_x'), cm:get_saved_value(PREFIX .. name .. '_y')
        if type(x) ~= 'number' or type(y) ~= 'number' then x, y = default_x, default_y end
        x, y = K.clamp(x, 0, max_x), K.clamp(y, 0, max_y)
        u:MoveTo(x, y)
        homes[name] = {x, y}
        if u.SetMoveable then pcall(function() u:SetMoveable(true) end) end
        return x, y
    end

    local function create_ui()
        local root = core:get_ui_root()
        if panel then left_home(panel, 'panel'); V.capture(); panel:Destroy() end
        if launcher then left_home(launcher, 'launcher'); launcher:Destroy() end
        gen = gen + 1
        panel, launcher, rows_parent, overlay_parent, title_ui, rail_count_ui = nil, nil, nil, nil, nil, nil
        search, status_ui, status_dot_ui, list_ui = nil, nil, nil, nil
        current_rows, strategic_inputs = {}, {}
        for k in pairs(ctx.controls) do ctx.controls[k] = nil end
        local rw, rh = K.compute_scale(root)
        local panel_h = ui.collapsed and K.RAIL_H or K.PANEL_H
        local pw, ph = px(ui.collapsed and RAIL_W or DESIGN_W), px(panel_h)
        local lw, lh = px(K.LAUNCHER_W), px(K.LAUNCHER_H)

        launcher = K.make(root, 'open', 'ui/wh3pt/button', 'button', 0, 0, lw, lh,
            '◆ 修改器 [F9]', ui.visible and 'selected' or 'active')
        place(launcher, 'launcher', launcher_x(rw, lw), K.EDGE_TOP, rw - lw, rh - lh)
        ctx.controls[PREFIX .. 'open'] = launcher
        panel = K.make(root, 'panel', 'ui/wh3pt/card', 'card', 0, 0, pw, ph, '', 'panel_bg')
        K.set_origin(place(panel, 'panel', math.max(0, rw - K.EDGE_RIGHT - pw), K.EDGE_TOP + px(K.LAUNCHER_H + K.LAUNCHER_GAP),
            rw - pw, rh - ph))

        local inner_h = panel_h - 2 * FRAME_INSET
        card(panel, 'rail_bg', FRAME_INSET, FRAME_INSET, RAIL_W - FRAME_INSET * (ui.collapsed and 2 or 1), inner_h, '', 'rail_bg')
        card(panel, 'logo_box', (RAIL_W - 34) / 2, 10, 34, 34, '◆', 'logo')
        for i, item in ipairs(PAGES) do
            button(panel, 'tab_' .. item.name, 8, 56 + (i - 1) * 36, RAIL_W - 16, 32, item.name,
                (not ui.collapsed and ui.group == item.name) and 'selected' or 'ghost')
        end
        if ui.collapsed then
            rail_count_ui = button(panel, 'rail_count', 8, K.RAIL_H - 86, RAIL_W - 16, 30, '0 项', 'active')
            button(panel, 'collapse_rail', 8, K.RAIL_H - 48, RAIL_W - 16, 36, '‹', 'active')
        else
            rail_count_ui = button(panel, 'rail_count', 8, K.FOOTER_Y + 8, RAIL_W - 16, 30, '0 项', 'active')
            card(panel, 'rail_div', RAIL_W, FRAME_INSET, 1, inner_h, '', 'divider')
            title_ui = label(panel, 'title', CX, 12, CW - 156, 30, PAGES[ui.group].title, 'title')
            button(panel, 'ui_scale', CX + CW - 146, 13, 76, 28, K.scale_label(), 'ghost_page')
            button(panel, 'collapse', CX + CW - 64, 13, 30, 28, '—', 'active')
            button(panel, 'close', CX + CW - 30, 13, 30, 28, '×', 'active')
            card(panel, 'header_div', RAIL_W + 1, 51, DESIGN_W - RAIL_W - 1 - FRAME_INSET, 1, '', 'divider')
            card(panel, 'footer_div', RAIL_W + 1, K.FOOTER_Y, DESIGN_W - RAIL_W - 1 - FRAME_INSET, 1, '', 'divider')
            status_dot_ui = card(panel, 'status_dot', CX, K.FOOTER_Y + 19, 8, 8, '', M.status_error and 'danger_dot' or 'ok_dot')
            status_ui = label(panel, 'status', CX + 16, K.FOOTER_Y + 8, CW - 16, 30, M.status, M.status_error and 'danger' or 'muted')
        end

        render()
        panel:PropagatePriority(200)
        panel:SetVisible(ui.visible)
    end
    V.create_ui = create_ui

    -- ------------------------------------------------------------------
    -- Countdown for the two-step clear and the undo window.
    -- ------------------------------------------------------------------
    local function arm_clear()
        local token = gen
        ui.armed = {left = 4, token = token}
        local armed = ui.armed
        local function tick()
            later(1000, function()
                if ui.armed ~= armed or token ~= gen then return end
                armed.left = armed.left - 1
                if armed.left <= 0 then ui.armed = nil end
                if ui.visible then ctx.guarded(render) end
                if ui.armed then tick() end
            end)
        end
        tick()
    end

    local function offer_undo(title, snapshot)
        local token = {title = title, snapshot = snapshot}
        ui.undo = token
        later(12000, function()
            if ui.undo == token then
                ui.undo = nil
                if ui.visible then ctx.guarded(render) end
            end
        end)
    end

    local function close_panel()
        V.capture()
        ui.visible, ui.sheet, ui.overlay, ui.armed = false, false, nil, nil
        if panel then panel:SetVisible(false) end
        if launcher then refit(launcher, '◆ 修改器 [F9]', 'active') end
    end

    local function go_page(name)
        ui.group, ui.sheet, ui.overlay, ui.armed = name, false, nil, nil
        ui.query, ui.search_text, ui.search_open, ui.only_favourites = '', '', false, false
        if search then search:SetStateText('') end
    end

    -- ------------------------------------------------------------------
    -- Click routing. Ids keep their ASCII prefixes; the localized rest is
    -- never sliced by the native UTF-8 string functions.
    -- ------------------------------------------------------------------
    function V.click(id)
        local previous = ctx.faction_name()
        ctx.faction()
        if previous ~= ctx.faction_name() then
            render()
            ctx.message('已切换到当前玩家阵营，请重新选择操作')
            return
        end
        if id == 'open' then
            if ui.visible then close_panel() else
                ui.visible, ui.sheet = true, false
                create_ui()
            end
            return
        elseif id == 'close' then
            close_panel(); return
        elseif id == 'collapse' or id == 'collapse_rail' then
            V.capture()
            ui.collapsed, ui.sheet, ui.overlay, ui.armed = not ui.collapsed, false, nil, nil
            create_ui()
            return
        elseif id == 'ui_scale' then
            V.capture()
            ui.overlay = ui.overlay ~= 'scale' and 'scale' or nil
        elseif substring(id, 1, 6) == 'scale_' then
            V.capture()
            local step = K.UI_SCALE_STEPS[tonumber(substring(id, 7)) or 1] or 0
            K.pref = step
            cm:set_saved_value(PREFIX .. 'ui_scale', step)
            ui.overlay = nil
            create_ui()
            ctx.message(step > 0 and ('界面缩放：' .. tostring(math.floor(step * 100 + 0.5)) .. '%')
                or ('界面缩放：自动（检测 ' .. tostring(math.floor((K.info.auto or 1) * 100 + 0.5)) .. '%）'))
            return
        elseif id == 'rail_count' or id == 'sheet_open' then
            V.capture()
            ui.overlay, ui.armed = nil, nil
            if ui.collapsed then
                ui.collapsed, ui.sheet = false, true
                create_ui()
                return
            end
            ui.sheet = id == 'sheet_open' or not ui.sheet
        elseif id == 'sheet_close' then
            ui.sheet, ui.armed = false, nil
        elseif id == 'favourites_only' then
            V.capture()
            ui.only_favourites = not ui.only_favourites
        elseif id == 'clear_filter' then
            V.capture()
            ui.only_favourites, ui.query, ui.search_text = false, '', ''
        elseif id == 'search_open' then
            V.capture()
            ui.search_open = true
        elseif id == 'search_go' then
            ui.overlay = nil
            if search then ui.search_text = search:GetStateText(); ui.query = ui.search_text end
            ui.sheet = false
        elseif id == 'search_clear' or id == 'empty_clear' then
            ui.overlay = nil
            if ui.query == '' and ui.search_text == '' then ui.search_open = false end
            ui.query, ui.search_text = '', ''
            if search then search:SetStateText('') end
        elseif id == 'clear' then
            if ui.armed then
                ui.armed = nil
                offer_undo('全关', ctx.snapshot())
                ctx.clear_all()
                return
            end
            arm_clear()
        elseif id == 'undo' then
            local token = ui.undo
            ui.undo = nil
            if token then ctx.restore(token.snapshot, token.title); return end
        elseif substring(id, 1, 5) == 'gold_' then
            ctx.command('treasury', substring(id, 6))
            return
        elseif substring(id, 1, 4) == 'off_' then
            local key = substring(id, 5)
            if definitions[key] then ctx.apply(key, false, draft[key]); return end
        elseif id == 'native_toggle' then
            ctx.native_toggle(not M.native_enabled)
        elseif substring(id, 1, 10) == 'elite_tab_' then
            ctx.elite_tabs().select(substring(id, 11))
            ctx.arrange_native_cards()
            return
        elseif id == 'resource_prev' or id == 'resource_next' then
            V.capture(); ui.home_resource_page = math.max(1, ui.home_resource_page + (id == 'resource_next' and 1 or -1))
        elseif substring(id, 1, 12) == 'resource_do_' then
            V.capture()
            local index = tonumber(substring(id, 13))
            ctx.set_resource(index, strategic_draft['resource_' .. index]); return
        elseif id == 'strategy_filter' then
            V.capture(); ui.own_regiments = not ui.own_regiments
            ui.strategic_category, ui.overlay = 'all', nil
        elseif id == 'strategy_sort' then
            V.capture(); ui.strategic_sort = ui.strategic_sort == 'recruitment_cost' and 'upkeep_cost' or ui.strategic_sort == 'upkeep_cost' and 'category' or 'recruitment_cost'
            ui.overlay = nil
        elseif id == 'strategy_categories' then
            V.capture(); ui.overlay = ui.overlay ~= 'category' and 'category' or nil
        elseif substring(id, 1, 12) == 'strategycat_' then
            V.capture(); ui.strategic_category = substring(id, 13)
            ui.overlay = nil
        elseif substring(id, 1, 12) == 'strategy_do_' then
            V.capture()
            ctx.native_select(tonumber(substring(id, 13)))
            return
        elseif id == 'income_open' then
            V.capture(); go_page('内政')
        elseif substring(id, 1, 12) == 'incomequick_' then
            V.capture(); ctx.apply('income', true, substring(id, 13)); return
        elseif id == 'preset_apply' then
            local kind = ui.overlay == 'preset_campaign' and 'campaign' or ui.overlay == 'preset_battle' and 'battle'
            ui.overlay = nil
            if kind then
                offer_undo(kind == 'campaign' and '战役发育预设' or '精锐战斗预设', ctx.snapshot())
                ctx.apply_preset(ctx.presets[kind], kind == 'campaign' and '战役发育预设' or '精锐战斗预设')
                return
            end
        elseif id == 'preset_cancel' then
            ui.overlay = nil
        elseif substring(id, 1, 4) == 'tab_' then
            V.capture()
            local was_collapsed = ui.collapsed
            go_page(substring(id, 5))
            ui.collapsed = false
            if was_collapsed then create_ui(); return end
        elseif id == 'cmd_preset_campaign' or id == 'cmd_preset_battle' then
            V.capture()
            ui.overlay = id == 'cmd_preset_campaign' and 'preset_campaign' or 'preset_battle'
        elseif substring(id, 1, 4) == 'cmd_' then
            V.capture()
            local key = substring(id, 5)
            ctx.command(key, cmd_draft[key])
            return
        elseif substring(id, 1, 6) == 'cplus_' or substring(id, 1, 7) == 'cminus_' then
            V.capture()
            local is_minus = substring(id, 1, 7) == 'cminus_'
            local key = substring(id, is_minus and 8 or 7)
            local cmd = command_defs[key]
            if not cmd then return end
            local n = ctx.validate(cmd, cmd_draft[key])
            n = math.max(cmd.step, math.min(cmd.max, n + (is_minus and -cmd.step or cmd.step)))
            cmd_draft[key] = tostring(n)
            for _, row in ipairs(current_rows) do
                if row.is_cmd and row.def.id == key and row.input then row.input:SetStateText(cmd_draft[key]) end
            end
        else
            local action, key = string.match(id, '^([^_]+)_(.+)$')
            local def = definitions[key]
            if action == 'fav' and (def or command_defs[key]) then
                V.capture()
                favourites[key] = not favourites[key]
                cm:set_saved_value(ctx.setting(key, 'favourite'), favourites[key])
            elseif not def then
                return
            elseif action == 'plus' or action == 'minus' or action == 'choice' then
                V.capture()
                local n = ctx.validate(def, draft[key])
                local low = def.min or (def.choices and 1 or 0)
                n = math.max(low, math.min(def.max, n + (action == 'minus' and -def.step or def.step)))
                if action == 'choice' and n == tonumber(draft[key]) then n = 1 end
                draft[key] = tostring(n)
                for _, row in ipairs(current_rows) do if row.def.id == key and row.input then row.input:SetStateText(draft[key]) end end
            elseif action == 'toggle' then
                V.capture()
                local on, actual = ctx.read(def)
                -- A typed value the player has not applied yet is not thrown away by
                -- the state button: it shows the 应用 / 还原 line instead.
                if on and not def.toggle and tonumber(draft[key]) ~= actual then
                    ctx.message('数值已修改：点「应用」生效，或「还原」放弃')
                else
                    ctx.apply(key, not on, draft[key])
                    return
                end
            elseif action == 'apply' then
                V.capture()
                ctx.apply(key, true, draft[key])
                return
            elseif action == 'revert' then
                V.capture()
                local on, actual = ctx.read(def)
                if on then draft[key] = tostring(actual); V.sync_input(key) end
            end
        end
        render()
    end

    -- A new player faction gets a fresh UI state; saved drafts are the model's.
    function V.reset()
        ui.query, ui.search_text, ui.search_open, ui.only_favourites = '', '', false, false
        ui.home_resource_page, ui.own_regiments = 1, true
        ui.strategic_category, ui.overlay, ui.sheet, ui.armed, ui.undo = 'all', nil, false, nil, nil
        strategic_inputs, strategic_draft, current_rows, search = {}, {}, {}, nil
        for index, def in ipairs(STRATEGIC.resources) do
            strategic_draft['resource_' .. index] = cm:get_saved_value(ctx.setting('resource_' .. def.id, 'target'))
        end
        close_list()
    end

    function V.layout_info()
        local S, TS = K.scale()
        return {scale = S, text_scale = TS, auto = K.info.auto, clamped = K.info.clamped,
            probe_w = K.info.probe_w, res_w = K.info.res_w, root_w = K.info.root_w, panel_h = K.PANEL_H}
    end

    function V.launcher() return launcher end
    function V.panel() return panel end
    function V.left_home(u, name) return left_home(u, name) end
    function V.store_point(u, name) return store_point(u, name) end
    return V
end
