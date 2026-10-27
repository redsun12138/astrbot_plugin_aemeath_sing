# 爱弥斯·点唱

让爱弥斯一段一段唱她自己的歌。音频来自预先切好的片段，**不调用 CosyVoice，不需要 GPU**，和 `astrbot_plugin_aemeath_voice` 独立共存。

## 触发方式

不用必须发命令，直接说：

```text
我想听你唱歌
唱首歌
来一首
唱一段
想听歌
```

她也会在聊天中按概率主动唱一句；主动唱有冷却和安静时段，避免刷屏。

命令兜底：

```text
/唱              唱下一段；当前歌结束后自动下一首
/唱 小小奇迹     从指定歌开始
/唱 列表         查看歌单
/唱 状态         查看当前进度
/唱 停止         停止当前点唱
/歌单            查看歌单
```

- 每次只发一条语音，约 10–19 秒，相当于“几句”。
- 进度按会话保存，下次接着唱。
- 一首唱完后，下一次自动下一首。

## 主动唱配置

```text
auto_sing_enabled           是否允许主动唱
auto_sing_percent           每条消息触发概率，默认 3%
auto_sing_cooldown_minutes  两次主动唱最小间隔，默认 60 分钟
auto_sing_quiet_start       安静时段开始，默认 23:30
auto_sing_quiet_end         安静时段结束，默认 08:00
auto_sing_intro             主动唱前说的一句话
```

## 歌曲

当前包含：

- 小小奇迹
- 星炬不熄
- 极限迪斯科
- 碎花
- 纸飞机
- 远航星的告别
- 那颗星梦见的春日
- 靛青宇宙

音频按人声频段能量低谷切分，尽量避免切在半句中间；如果个别段落不自然，可以重新切。

## 安装

把整个目录放到：

```text
AstrBot/data/plugins/astrbot_plugin_aemeath_sing
```

重启 AstrBot。音频随插件放在插件目录，播放进度保存在：

```text
AstrBot/data/plugin_data/astrbot_plugin_aemeath_sing/state.json
```

## 说明

- QQ 语音一条就是一个音频文件，不能流式“边唱边生成”。
- 这里播放的是原唱片段，不是 AI 翻唱；不需要 RVC/SVC。
- 想改成别的歌：替换 `songs/<slug>/` 下的 wav，并更新 `manifest.json`。
- 片段格式为 WAV 单声道 24kHz，QQ 语音兼容性最好。

## 许可证

AGPL-3.0。歌曲版权归原权利人所有，仅用于个人学习/娱乐。
