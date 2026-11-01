-- UI kit for the campaign panel: design-unit geometry, scale calibration, text
-- fitting and the component constructors. Everything here is engine plumbing;
-- what the panel shows lives in views.lua. One closure keeps the main chunk's
-- local-variable budget free (Lua 5.1 allows 200 per function).
local function create_kit(env)
    local PREFIX = env.prefix
    local FONTS = type(env.fonts) == 'table' and env.fonts or {}
    local controls = env.controls
    -- WH3 replaces some string functions with UTF-8 variants; the originals
    -- stay reachable under *_lua. Byte slicing is only used on ASCII prefixes.
    local substring = string.sub_lua or string.sub
    local byte_fn = string.byte_lua or string.byte
    local K = {}

    -- Design units: the layout at 100% UI scale, relative to the panel origin.
    K.DESIGN_W, K.DESIGN_H, K.MAX_H = 520, 640, 760
    K.RAIL_W = 68
    K.CX = K.RAIL_W + 12                  -- content column left edge
    K.CW = K.DESIGN_W - K.CX - 14         -- content column width
    K.ROW_W = K.CW - 18                   -- list rows; the rest is the native scrollbar
    K.FRAME_INSET = 4
    K.RAIL_H = 56 + (env.pages + 1) * 36 + 92   -- collapsed panel height
    K.DRAG_SLOP = 6                       -- screen pixels a press may wander and stay a click
    K.LAUNCHER_W, K.LAUNCHER_H, K.LAUNCHER_GAP = 116, 30, 8
    K.LAUNCHER_CLEARANCE = 56             -- kept free left of the native tab strip
    K.EDGE_RIGHT, K.EDGE_TOP, K.EDGE_BOTTOM = 16, 16, 12
    K.S_MIN, K.S_MAX = 1.0, 2.0
    K.UI_SCALE_STEPS = {0, 1.0, 1.25, 1.5, 1.75, 2.0}   -- 0 = automatic
    -- Ten CJK glyphs in the 14px 'probe' state measure ~140 units at 100%.
    local PROBE_TEXT, PROBE_CHARS, PROBE_FONT = '战锤修改器界面校准用', 10, 14

    local S, TS, ox, oy = 1, 1, 0, 0
    K.pref, K.info = 0, {}
    local meta = setmetatable({}, {__mode = 'k'})

    function K.layout(panel_h)
        K.PANEL_H = panel_h
        K.FOOTER_Y = panel_h - 46
        K.CONTENT_BOTTOM = K.FOOTER_Y - 8
    end
    K.layout(K.DESIGN_H)

    function K.px(v) return math.floor(v * S + 0.5) end
    function K.clamp(v, lo, hi) return math.max(lo, math.min(hi, v)) end
    function K.scale() return S, TS end
    function K.origin() return ox, oy end
    function K.set_origin(x, y) ox, oy = x, y end
    local px, clamp = K.px, K.clamp

    -- ---------------------------------------------------------------------
    -- UTF-8 helpers: trust byte access only after a self-test, else a verified
    -- gmatch split, else no truncation at all.
    -- ---------------------------------------------------------------------
    local BYTES_OK = (function()
        local ok, res = pcall(function()
            local s = '\228\184\173a'
            return #s == 4 and byte_fn(s, 1) == 228 and byte_fn(s, 2) == 184 and byte_fn(s, 4) == 97
                and substring(s, 4, 4) == 'a' and substring(s, 1, 3) == '\228\184\173'
        end)
        return ok and res == true
    end)()

    function K.utf8_chars(text)
        local chars = {}
        if BYTES_OK then
            local i, n = 1, #text
            while i <= n do
                local c = byte_fn(text, i)
                local len = (c < 0x80 and 1) or (c >= 0xF0 and 4) or (c >= 0xE0 and 3) or (c >= 0xC0 and 2) or 1
                chars[#chars + 1] = substring(text, i, math.min(n, i + len - 1))
                i = i + len
            end
            return chars
        end
        local ok = pcall(function()
            for ch in string.gmatch(text, '[%z\1-\127\192-\253][\128-\191]*') do chars[#chars + 1] = ch end
        end)
        if ok and table.concat(chars) == text then return chars end
        return nil
    end
    local utf8_chars = K.utf8_chars

    -- Width estimate without TextDimensions: CJK ~1 em, 2-byte symbols ~0.7 em, ASCII ~0.62 em.
    local function estimate_width(text, font_px)
        local chars, units = utf8_chars(text), 0
        if chars then
            for _, ch in ipairs(chars) do
                local n = #ch
                units = units + ((n >= 3 and 1.0) or (n == 2 and 0.7) or 0.62)
            end
        else
            units = #text * 0.62
        end
        return units * font_px * TS
    end

    local function font_of(kind, state)
        local t = FONTS[kind]
        return (t and t[state]) or 13
    end

    local function measure(u)
        local fn = u.TextDimensions
        if type(fn) ~= 'function' then return nil end
        local ok, w, h, lines = pcall(fn, u)
        if ok and type(w) == 'number' and w > 0 then
            return w, (type(h) == 'number' and h or nil), (type(lines) == 'number' and lines or nil)
        end
        return nil
    end
    K.measure = measure

    function K.set_state(u, state)
        if state and u.SetState then pcall(function() u:SetState(state) end) end
    end
    local set_state = K.set_state

    function K.tooltip(u, text)
        if u.SetTooltipText then pcall(function() u:SetTooltipText(text, true) end) end
    end

    -- width_ok, height_ok, measured_width for the text currently set.
    local function check(u, m, state, text, inner_w)
        local w, h, lines = measure(u)
        if not w then
            w = estimate_width(text, font_of(m.kind, state))
            return w <= inner_w, true, w
        end
        local one_line = (not lines or lines <= 1) and (not h or h < font_of(m.kind, state) * TS * 2)
        return w <= inner_w and one_line, (not h or h <= m.h + 1), w
    end

    -- Set text so it never exceeds the component: base state, then the *_sm
    -- smaller-font state, then UTF-8 safe truncation with an ellipsis.
    function K.fit_text(u, text)
        text = tostring(text == nil and '' or text)
        local m = meta[u]
        if not m then u:SetStateText(text); return 0 end
        local candidates = {m.base}
        local t = FONTS[m.kind]
        if m.base and t and t[m.base .. '_sm'] then candidates[2] = m.base .. '_sm' end
        local inner_w = math.max(1, m.w - 2 * px(m.kind == 'button' and 4 or 1))
        local state, width = m.base, 0
        for i, st in ipairs(candidates) do
            set_state(u, st); m.cur = st
            u:SetStateText(text)
            if text == '' then m.text_w = 0; return 0 end
            local w_ok, h_ok, w = check(u, m, st, text, inner_w)
            state, width = st, w
            if w_ok and (h_ok or i == #candidates) then m.text_w = w; return w end
        end
        local chars = utf8_chars(text)
        if not chars or #chars < 2 then m.text_w = width; return width end
        local lo, hi, best = 0, #chars - 1, 0
        while lo <= hi do
            local mid = math.floor((lo + hi) / 2)
            local candidate = table.concat(chars, '', 1, mid) .. '…'
            u:SetStateText(candidate)
            local w_ok, _, w = check(u, m, state, candidate, inner_w)
            if w_ok then best, width, lo = mid, w, mid + 1 else hi = mid - 1 end
        end
        local shown = table.concat(chars, '', 1, best) .. '…'
        u:SetStateText(shown)
        if best == 0 then local _, _, w = check(u, m, state, shown, inner_w); width = w end
        K.tooltip(u, text)
        m.text_w = width
        return width
    end
    local fit_text = K.fit_text

    function K.refit(u, text, state)
        local m = meta[u]
        if m then
            if state then m.base = state end
            return fit_text(u, text)
        end
        set_state(u, state)
        u:SetStateText(tostring(text))
        return 0
    end

    function K.text_w(u)
        local m = meta[u]
        return (m and m.text_w or 0) / S
    end

    function K.shrink_to_text(u)
        local m = meta[u]
        if not m then return 0 end
        if m.text_w and m.text_w > 0 then
            local w = math.min(m.w, math.ceil(m.text_w) + 2 * px(6))
            if w < m.w then u:Resize(w, m.h); m.w = w end
        end
        return m.w / S
    end

    function K.shrink_right(u)
        local m = meta[u]
        if not m then return 0 end
        if m.text_w and m.text_w > 0 then
            local w = math.min(m.w, math.ceil(m.text_w) + 2 * px(6))
            if w < m.w then
                local x, y = u:Position()
                u:MoveTo(x + m.w - w, y); u:Resize(w, m.h); m.w = w
            end
        end
        return m.w / S
    end

    -- Low level constructor in screen pixels.
    function K.make(parent, id, template, kind, x, y, w, h, text, state)
        local u = UIComponent(parent:CreateComponent(PREFIX .. id, template))
        u:SetDockingPoint(0)
        if u.SetDockOffset then u:SetDockOffset(0, 0) end
        u:SetCanResizeWidth(true); u:SetCanResizeHeight(true); u:Resize(w, h)
        set_state(u, state)
        if template == 'ui/common ui/text_box' and u.ResizeCurrentStateImage then
            pcall(function() u:ResizeCurrentStateImage(0, w, h) end)
        end
        u:MoveTo(x, y)
        if kind then meta[u] = {kind = kind, base = state, cur = state, w = w, h = h} end
        if text ~= nil then
            if kind then fit_text(u, text) else u:SetStateText(tostring(text)) end
        end
        u:PropagatePriority(200)
        return u
    end
    local make = K.make

    -- Design-unit constructor relative to the panel origin. Edges are rounded
    -- so a child inside the panel in design units stays inside in pixels.
    function K.component(parent, id, template, kind, dx, dy, dw, dh, text, state)
        local x0, y0 = px(dx), px(dy)
        local w = math.max(1, px(dx + dw) - x0)
        local h = math.max(1, px(dy + dh) - y0)
        return make(parent, id, template, kind, ox + x0, oy + y0, w, h, text, state)
    end
    local component = K.component

    function K.label(parent, id, dx, dy, dw, dh, text, state)
        return component(parent, id, 'ui/wh3pt/label', 'label', dx, dy, dw, dh, text, state or 'active')
    end
    function K.card(parent, id, dx, dy, dw, dh, text, state)
        return component(parent, id, 'ui/wh3pt/card', 'card', dx, dy, dw, dh, text or '', state or 'active')
    end
    function K.button(parent, id, dx, dy, dw, dh, text, state)
        local u = component(parent, id, 'ui/wh3pt/button', 'button', dx, dy, dw, dh, text, state or 'active')
        controls[PREFIX .. id] = u
        return u
    end
    function K.input(parent, id, dx, dy, dw, dh, text)
        return component(parent, id, 'ui/common ui/text_box', nil, dx, dy, dw, dh, text)
    end
    function K.icon(parent, id, dx, dy, dw, dh)
        return component(parent, id, 'ui/wh3pt/icon', nil, dx, dy, dw, dh, '', 'active')
    end

    -- ---------------------------------------------------------------------
    -- Scale calibration. root:Dimensions() is the space MoveTo/Resize use and
    -- the clamp for the panel. A probe label measures the text scale when
    -- TextDimensions exists; otherwise S is 1 unless the player chose a scale.
    -- ---------------------------------------------------------------------
    local function calibrate(root, rw, rh)
        local info = {root_w = rw, root_h = rh}
        local ok_res, res_w, res_h = pcall(function()
            if core and type(core.get_screen_resolution) == 'function' then return core:get_screen_resolution() end
        end)
        if ok_res and type(res_w) == 'number' and res_w > 0 then
            info.res_w, info.res_h = res_w, res_h
            info.space_ratio = res_w / math.max(1, rw)
        end
        pcall(function()
            local p = UIComponent(root:CreateComponent(PREFIX .. 'probe', 'ui/wh3pt/label'))
            local ok_m, w = pcall(function()
                p:SetCanResizeWidth(true); p:SetCanResizeHeight(true)
                p:Resize(math.max(800, rw), 80)
                set_state(p, 'probe')
                p:SetStateText(PROBE_TEXT)
                return measure(p)
            end)
            p:Destroy()
            if ok_m and w then info.probe_w = w end
        end)
        if info.probe_w then info.text_ratio = info.probe_w / (PROBE_CHARS * PROBE_FONT) end
        return info
    end

    function K.compute_scale(root)
        local rw, rh = root:Dimensions()
        local info = calibrate(root, rw, rh)
        local auto = 1
        if info.text_ratio then auto = clamp(math.floor(info.text_ratio * 20 + 0.5) / 20, K.S_MIN, K.S_MAX) end
        local wanted = K.pref > 0 and K.pref or auto
        local fit_w = (rw - 2 * K.EDGE_RIGHT) / K.DESIGN_W
        local fit_h = (rh - K.EDGE_TOP - K.EDGE_BOTTOM) / (K.LAUNCHER_H + K.LAUNCHER_GAP + K.DESIGN_H)
        -- Clamped to the screen: geometry shrinks uniformly and fit_text steps
        -- fonts down or truncates so nothing overflows.
        S = math.max(0.5, math.min(wanted, fit_w, fit_h))
        TS = info.text_ratio and math.max(0.5, info.text_ratio) or wanted
        info.auto, info.wanted, info.scale, info.text_scale = auto, wanted, S, TS
        info.clamped = S < wanted - 0.001
        K.info = info
        -- Tall screens get a taller panel, so the list shows more rows.
        local avail = (rh - K.EDGE_TOP - K.EDGE_BOTTOM) / S - K.LAUNCHER_H - K.LAUNCHER_GAP
        K.layout(math.max(K.DESIGN_H, math.min(K.MAX_H, math.floor(avail))))
        return rw, rh
    end

    function K.scale_label()
        if K.pref > 0 then return '缩放 ' .. tostring(math.floor(K.pref * 100 + 0.5)) .. '%' end
        return '缩放 自动'
    end

    return K
end
