# 全面战争：战锤 III 修改器 · 2.9.0

单人战役修改器，包含两部分：进游戏后按 F9 打开的实时面板，以及开战前生成的静态配置包。修改通过游戏官方脚本接口和数据表完成，不改内存。

成品在 `dist/`，由 `Build.ps1` 生成，不进 git。功能和更新记录见 [内置修改器说明](docs/内置修改器说明.txt)，操作步骤见 [使用说明](docs/使用说明.txt)。

## 目录

| 路径 | 内容 |
| --- | --- |
| `src/` | Python 源码、`schema.json` 表结构 |
| `src/ingame/` | 游戏内 Lua 脚本（`panel.lua` 模型、`views.lua` 界面、`kit.lua` 构造与缩放）和界面模板 |
| `reference/` | 构建时读取的参考包，`workshop/` 是创意工坊基础包的备用副本 |
| `tests/` | 离线测试和 Lua 接口模拟 |
| `tools/` | 开发工具 |
| `docs/` | 随成品分发的说明 |
| `dist/` | 成品：两个 EXE、参考包、说明（构建生成） |
| `output/` | 生成的 Pack 和预览图（构建生成） |

本机设置 `settings.json` 和安装备份 `history/` 也不进 git。

## 构建

需要 Python 3.14 和本机安装的游戏。测试和构建都会只读游戏数据，默认路径是 `src/trainer_core.py` 里的 `GAME`。

```powershell
pip install -r requirements-dev.txt
.\Build.ps1
```

脚本依次运行全部测试、生成 Pack、打包两个 EXE、把参考包和说明复制到 `dist/`，最后让每个 EXE 用 `--smoke` 自己完整重建一次 Pack。任何一步失败都会中止。只跑测试：

```powershell
python -m unittest discover -s tests -t .
```

## 游戏更新后

修改器包是 Movie 类型（`pack_type=4`），游戏总会加载，启动器无法关闭。过期的数据表可能让游戏无法启动，所以 Pack 里记录了生成它的游戏数据（`wh3pt_build.json`），由 `src/game_compat.py` 比对。

使用时：打开安装器。提示“需要重新适配”就点“重新适配并安装”，它会按当前游戏数据重新生成；失败时先卸载，保证游戏能启动。

报错缺少表结构时，说明游戏用了新的表版本：

```powershell
python tools/extract_schema.py --download
.\Build.ps1
```

字段只增不减的表版本升级会自动迁移，结果写入生成说明。字段被改动或删除时构建会报“需要人工迁移”，需要核对后加入 `src/trainer_core.py` 的 `REVIEWED_MIGRATIONS`。

直接从 Steam 启动时无法拦截，这是 Movie 包方案的限制。

## 开发注意

- 游戏的 Lua 是 5.1。`wh3pt_panel.lua` 由多个文件拼成一个代码块，每个函数最多 200 个局部变量，所以界面代码放在 `kit.lua`（构造、字号适配、缩放校准）和 `views.lua`（页面、行模型、路由、覆盖层）各自的闭包里，`panel.lua` 主块只保留模型和指令。新增内容放进这些闭包或已有的表里；超限时测试会直接报错。
- 组件销毁后不能再调用它的方法，`pcall` 接不住崩溃。测试里的 mock 会记录这类调用，`tearDown` 发现后判失败。
- 离线测试通过不代表游戏内正常，游戏内效果需要实机验证。
