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

她不会因为“冷却结束”或“你随便回了一句”就唱。只有你明确想听，或你求鼓励/奖励时才唱。

求鼓励/奖励也算点唱：

```text
鼓励我一下
奖励我
夸夸我
给我打气
安慰我
```

## 她自己想唱（[sing] 标记）

插件会在每次 LLM 请求里注入一条提示：

```text
想唱歌时，在回复最后加 [sing]
```

模型觉得合适时才会加，比如：

- 你难过、需要安慰
- 你想被鼓励/奖励
- 她单纯想唱一段

插件检测到 `[sing]` 后会：

1. 把标记从文字里删掉
2. 等 2 秒
3. 发一段歌

默认 20 分钟内最多自主唱一次，避免每句都唱。

```text
self_sing_enabled           是否允许 [sing] 自主唱
self_sing_cooldown_minutes  两次自主唱最小间隔
self_sing_delay_seconds     文字后等几秒发歌
self_sing_intro             自主唱前说的一句话
```

如果希望她偶尔在回复主动消息后自己唱，可以把 `proactive_reply_percent` 调高；默认关闭。

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

## 主动回唱（默认关闭）

她**不会**在任何一条消息后随机唱，也不会冷却一到就唱。

默认情况下，只有你明确说想听，或说“鼓励我/奖励我/夸夸我”时才唱。

如果你希望她偶尔在回复主动消息后自己唱，可以手动打开：

```text
proactive_reply_enabled        是否允许回复主动消息后唱
proactive_reply_percent        触发概率，默认 0（关闭）；100=每次回复都唱
proactive_reply_window_minutes 回复时限，默认 30 分钟
proactive_reply_delay_seconds  回复后等几秒再发歌
proactive_reply_intro          唱之前说的一句话
```

## 歌曲

当前包含：

- 小小奇迹
- 星炬不熄
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
