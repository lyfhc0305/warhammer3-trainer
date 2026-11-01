-- Strict offline model/UI boundary; this is not a game simulator.
-- The game's restricted Lua math library omits this standard constant.
math.huge = nil
-- The native library also preserves standard byte slicing under this alias.
string.sub_lua = string.sub
-- WH3's UTF-8 find accepts three arguments and searches literal text.
string.find_lua = string.find
function string.find(s, needle, start, ...)
    assert(select('#', ...) == 0, 'native string.find only accepts three arguments')
    return string.find_lua(s, needle, start, true)
end
widgets, queue, saved, bundles, logs, stale_ui_calls = {}, {}, {}, {}, {}, {}
multiplayer, fail_read, ignore_apply, ignore_remove = false, false, false, false
screen_width, screen_height = 1920, 1080
treasury_balance = 5000
regular_income, campaign_turn = 1000, 1
-- Optional native text measurement. text_scale emulates the game's UI scale
-- (fonts grow, script geometry does not); set text_dimensions_enabled=false
-- before creating widgets to emulate a build without TextDimensions.
-- UI_FONT_TABLE (template kind -> state -> px) is supplied by the harness.
-- text_wraps emulates TextDimensions on wrapping text: it reports the laid-out
-- block (narrow and tall) and no line count, as the native call does.
text_scale, text_dimensions_enabled, text_wraps = 1.0, true, false
widget_seq = 0
function measure_text(text, font_px)
    local units, i, n = 0, 1, #text
    while i <= n do
        local c = text:byte(i)
        local len = c < 0x80 and 1 or c >= 0xF0 and 4 or c >= 0xE0 and 3 or 2
        units = units + (len >= 3 and 1.0 or len == 2 and 0.7 or 0.6)
        i = i + len
    end
    return units * font_px * text_scale
end
local function template_kind(template)
    if template == 'ui/common ui/text_box' then return 'text_box' end
    return template and template:match('^ui/wh3pt/(%a+)$')
end
function font_px_of(w)
    local t = UI_FONT_TABLE and UI_FONT_TABLE[template_kind(w.template)]
    return (t and t[w.state]) or 14
end
replenished_characters, character_xp = {}, {}
local function list(items)
    return {num_items=function() return #items end, item_at=function(_,i) return items[i+1] end}
end
local function widget(id,parent,template)
    widget_seq = widget_seq + 1
    local w={id=id,parent=parent,template=template,children={},text='',state='active',x=0,y=0,w=200,h=32,visible=true,seq=widget_seq}
    widgets[id]=w
    if text_dimensions_enabled then
        function w:TextDimensions()
            if self.text == '' then return 0, 0, 0 end
            local px = font_px_of(self)
            local width, line_h = measure_text(self.text, px), px * 1.25 * text_scale
            if text_wraps and width > self.w then
                return self.w * 0.9, line_h * math.ceil(width / self.w), 1
            end
            return width, line_h, 1
        end
    end
    if parent then parent.children[#parent.children+1]=w end
    function w:CreateComponent(name,path)
        assert(
            path=='ui/wh3pt/label'
            or path=='ui/wh3pt/button'
            or path=='ui/wh3pt/card'
            or path=='ui/wh3pt/list'
            or path=='ui/wh3pt/icon'
            or path=='ui/templates/panel_frame'
            or path=='ui/common ui/text_box'
        )
        local created=widget(name,self,path)
        if path=='ui/wh3pt/list' then
            widget('list_box',widget('list_clip',created),nil)
            local slider=widget('vslider',created)
            slider.w=20
            widget('top',slider).h=22
        end
        return created
    end
    function w:Id() return self.id end
    -- A destroyed child is gone from its parent, as in the engine.
    function w:ChildCount()
        local count=0
        for _,c in ipairs(self.children) do if not c.dead then count=count+1 end end
        return count
    end
    function w:Visible() return self.visible and not self.dead end
    function w:Find(name)
        local index=-1
        for _,c in ipairs(self.children) do
            if not c.dead then
                index=index+1
                if index==name or c.id==name then return c end
            end
        end
    end
    function w:Layout() self.layouts=(self.layouts or 0)+1 end
    function w:SetMoveable(v) self.moveable=v end
    function w:IsDragged() return self.dragged==true end
    function w:SetProperty(name,value) self.properties=self.properties or {}; self.properties[name]=value end
    function w:Destroy() self.dead=true; for _,c in ipairs(self.children) do if not c.dead then c:Destroy() end end end
    function w:SetDockingPoint(n) assert(n==0) end
    function w:SetDockOffset(dx,dy) self.dock_dx=dx; self.dock_dy=dy end
    function w:SetCanResizeWidth(v) assert(v==true) end
    function w:SetCanResizeHeight(v) assert(v==true) end
    function w:Resize(width,height) self.w=width; self.h=height end
    function w:ResizeCurrentStateImage(idx,width,height) self.img_w=width; self.img_h=height end
    -- Children follow their parent, as in the engine.
    function w:MoveTo(x,y)
        local dx,dy=x-self.x,y-self.y
        self.x=x; self.y=y
        for _,c in ipairs(self.children) do if not c.dead then c:MoveTo(c.x+dx,c.y+dy) end end
    end
    function w:SetState(s)
        assert(type(s)=='string')
        local t = UI_FONT_TABLE and UI_FONT_TABLE[template_kind(self.template)]
        if t then assert(t[s], 'unknown state ' .. s .. ' for ' .. tostring(self.template)) end
        self.state=s
    end
    function w:CurrentState() return self.state end
    function w:SetImagePath(path,index,resize) self.image_path=path end
    function w:SetTooltipText(text,localised) self.tooltip=text end
    function w:SetStateText(text) assert(type(text)=='string'); self.text=text end
    function w:GetStateText() return self.text end
    function w:Position() return self.x,self.y end
    function w:Dimensions() if id=='root' then return screen_width,screen_height end; return self.w,self.h end
    function w:SetVisible(v) self.visible=v end
    function w:PropagatePriority(v)
        self.priority=v
        for _,c in ipairs(self.children) do if not c.dead then c:PropagatePriority(v) end end
    end
    function w:SetDisabled(v) self.disabled=v end
    -- Motion and highlight calls are recorded; a stale call is caught below like any other.
    function w:TriggerAnimation(name) assert(type(name)=='string'); self.animations=self.animations or {}; self.animations[#self.animations+1]=name end
    function w:SetOpacity(value,propagate) assert(type(value)=='number' and value>=0 and value<=255); self.opacity=value end
    function w:StartPulseHighlight(brightness,state) self.pulsing=true end
    function w:StopPulseHighlight(state) self.pulsing=false end
    -- Engine handles are raw pointers: a call after Destroy can crash and pcall
    -- cannot catch it, so record every such call for the harness to reject.
    for name,fn in pairs(w) do
        if type(fn)=='function' then
            w[name]=function(self,...)
                if self.dead then stale_ui_calls[#stale_ui_calls+1]=self.id..':'..name end
                return fn(self,...)
            end
        end
    end
    return w
end
root=widget('root')
function UIComponent(w) return w end
function out(s) logs[#logs+1]=s end
function ModLog(s) logs[#logs+1]=s end
local f={}
function f:is_null_interface() return false end
function f:is_human() return true end
function f:name() return 'player' end
function f:treasury() return treasury_balance end
function f:income()
    local amount=0
    local b=bundles.wh3pt_income
    if b then for _,e in ipairs(b.entries) do
        if e:key()=='wh_main_effect_economy_gdp_mod_all' then amount=e:value() end
    end end
    return math.floor(regular_income*(1+amount/100)+.5)
end
local function make_char(cqi,wounded,garrison)
    local ch={}
    function ch:cqi() return cqi end
    function ch:is_null_interface() return false end
    function ch:is_wounded() return wounded or false end
    function ch:faction() return f end
    function ch:has_military_force() return garrison ~= nil end
    function ch:military_force()
        return {is_armed_citizenry=function() return garrison or false end}
    end
    return ch
end
characters = {make_char(101,false,false), make_char(102,false,false), make_char(103,true,false), make_char(104,false,true)}
function f:character_list() return list(characters) end
regions = {}
function f:region_list() return list(regions) end
function f:effect_bundles()
    if fail_read then error('read unavailable') end
    local items={}; for _,b in pairs(bundles) do items[#items+1]=b end
    return list(items)
end
cm={}
function cm:turn_number() return campaign_turn end
function cm:is_multiplayer() return multiplayer end
function cm:is_local_players_turn(force) return true end
function cm:get_local_faction_name(force) return 'player' end
function cm:get_faction(name) assert(name=='player'); return f end
function cm:get_saved_value(key) return saved[key] end
function cm:set_saved_value(key,value) assert(type(value)~='table'); saved[key]=value end
function cm:add_first_tick_callback(fn) first_tick=fn end
function cm:callback(fn,delay) queue[#queue+1]=fn end
campaign_timers={}
function cm:remove_callback(name) campaign_timers[name]=nil end
function cm:repeat_callback(fn,interval,name) campaign_timers[name]=fn end
-- Real-time callbacks (UI motion, countdowns) run only when a test flushes them.
real_queue={}
function cm:real_callback(fn,interval,name) assert(type(interval)=='number' and interval>=0); real_queue[#real_queue+1]={fn=fn,ms=interval,name=name} end
function flush_real() local q=real_queue; real_queue={}; for _,item in ipairs(q) do item.fn() end end
function cm:char_lookup_str(cqi) return 'character_cqi:' .. tostring(cqi) end
function cm:treasury_mod(name,amount)
    assert(name=='player')
    treasury_balance = treasury_balance + amount
end
function cm:replenish_action_points(lookup)
    replenished_characters[#replenished_characters+1] = lookup
end
function cm:add_agent_experience(lookup,amount)
    character_xp[lookup] = (character_xp[lookup] or 0) + amount
end
function cm:create_new_custom_effect_bundle(key)
    assert(known_bundle_keys, 'Harness must load actual effect bundle definitions from the Pack')
    if not known_bundle_keys[key] then return {is_null_interface=function() return true end} end
    local b={entries={}}
    function b:key() return key end
    function b:is_null_interface() return false end
    function b:add_effect(effect,scope,value)
        local e={key=function() return effect end,scope=function() return scope end,value=function() return value end}
        self.entries[#self.entries+1]=e; return true
    end
    function b:set_duration(n) assert(n==0) end
    function b:effects() return list(self.entries) end
    return b
end
function cm:apply_custom_effect_bundle_to_faction(b,target) assert(target==f); if not ignore_apply then bundles[b:key()]=b end end
function cm:remove_effect_bundle(key,name) assert(name=='player'); if not ignore_remove then bundles[key]=nil end end
function flush() local q=queue; queue={}; for _,fn in ipairs(q) do fn() end end
core={listeners={}}
svr={}
function core:svr_save_bool(key,value) assert(type(value)=='boolean');svr[key]=value end
function core:svr_load_bool(key) return svr[key]==true end
function core:svr_save_string(key,value) assert(type(value)=='string');svr[key]=value end
function core:svr_load_string(key) return svr[key] or '' end
function core:get_ui_root() return root end
-- Optional native call; tests may set core.get_screen_resolution=nil.
function core:get_screen_resolution() return screen_width, screen_height end
function core:remove_listener(name) self.listeners[name]=nil end
function core:add_listener(name,event,condition,fn,persistent)
    self.listeners[name]={event=event,condition=condition,fn=fn}
end
function core:trigger_event(event,ctx)
    for _,l in pairs(self.listeners) do
        if l.event==event and (l.condition==true or l.condition(ctx)) then
            l.fn(ctx)
        end
    end
end

-- Engine-facing faction pool fixture: mutations never touch military forces.
native_pool, native_calls, reject_pool, pool_generation = {}, {}, false, 0
function f:mercenary_pool()
    local entries = {}
    for key, values in pairs(native_pool) do
        local entry_key, entry_values, generation = key, values, pool_generation
        local function valid() assert(generation==pool_generation, "stale native pool interface after mutation") end
        entries[#entries+1] = {
            unit_record=function() valid(); return {key=function() valid(); return entry_key end} end,
            unit_count=function() valid(); return entry_values.count end,
            current_max_units=function() valid(); return entry_values.max end
        }
    end
    return {is_null_interface=function() return false end, mercenary_pool_units=function() return list(entries) end}
end
function cm:add_unit_to_faction_mercenary_pool(target,key,source,count,chance,max_units,per_turn,restriction,subculture,tech,partial,group)
    assert(target==f and source=='wh3_main_regiments_of_renown_pool')
    assert(restriction=='player' and subculture=='' and tech=='' and group==key)
    assert(chance==0 and per_turn==0 and partial==false)
    native_calls[#native_calls+1] = key
    if not reject_pool then pool_generation=pool_generation+1; native_pool[key] = {count=count,max=max_units} end
end
