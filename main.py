"""
爱弥斯 · 点唱插件（AstrBot）

数据来自预先切好的歌曲片段，不调用 CosyVoice，也不需要 GPU。

指令：
    /唱              唱下一段；当前歌结束后自动下一首
    /唱 歌名         从指定歌开始
    /唱 列表         查看歌单
    /唱 状态         查看当前进度
    /唱 停止         停止当前点唱
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # 兼容旧版本
    def get_astrbot_data_path():
        return "data"


class AemeathSingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_aemeath_sing"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / "state.json"

        # 音频随插件一起分发，直接读插件目录；进度单独存 plugin_data
        bundled = Path(__file__).resolve().parent
        self.manifest_file = bundled / "manifest.json"
        self.songs_dir = bundled / "songs"

        self.manifest = self._load_manifest()
        self.state = self._load_state()
        logger.info(
            "[aemeath_sing] 点唱插件已加载：%s 首，%s 段",
            len(self.manifest.get("songs", [])),
            sum(len(s.get("chunks", [])) for s in self.manifest.get("songs", [])),
        )

    # ---------- 数据 ----------

    def _load_manifest(self) -> dict:
        if not self.manifest_file.exists():
            logger.error("[aemeath_sing] 找不到 manifest.json")
            return {"songs": []}
        try:
            return json.loads(self.manifest_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("[aemeath_sing] manifest 读取失败: %s", exc)
            return {"songs": []}

    def _load_state(self) -> dict:
        if not self.state_file.exists():
            return {"sessions": {}}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            if "sessions" not in data:
                data["sessions"] = {}
            return data
        except Exception:
            return {"sessions": {}}

    def _save_state(self) -> None:
        self.state_file.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _songs(self) -> list[dict]:
        return self.manifest.get("songs", [])

    def _find_song(self, query: str) -> dict | None:
        q = query.strip().lower()
        if not q:
            return None
        for song in self._songs():
            title = str(song.get("title", ""))
            slug = str(song.get("slug", ""))
            if q == title.lower() or q == slug.lower() or q in title.lower():
                return song
        return None

    def _session(self, umo: str) -> dict:
        sessions = self.state.setdefault("sessions", {})
        return sessions.setdefault(umo, {"song": "", "index": 0})

    def _chunk_path(self, slug: str, file: str) -> Path:
        return self.songs_dir / slug / file

    def _next_song(self, current_slug: str) -> dict | None:
        songs = self._songs()
        if not songs:
            return None
        for i, song in enumerate(songs):
            if song.get("slug") == current_slug:
                return songs[(i + 1) % len(songs)]
        return songs[0]

    # ---------- 播放 ----------

    async def _sing(self, event: AstrMessageEvent):
        if not self.config.get("enabled", True):
            return
        songs = self._songs()
        if not songs:
            yield event.plain_result("歌单还没准备好。")
            return

        text = (event.message_str or "").strip()
        for prefix in (
            "/继续唱",
            "继续唱",
            "/再唱",
            "再唱",
            "/接着唱",
            "接着唱",
            "/唱歌",
            "/唱",
            "唱歌",
            "唱",
        ):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        arg = text
        if arg in {"首歌", "一段", "一首", "歌", "继续", "接着"}:
            arg = ""
        umo = event.unified_msg_origin
        session = self._session(umo)

        if arg in {"停止", "停", "结束"}:
            self.state.get("sessions", {}).pop(umo, None)
            self._save_state()
            yield event.plain_result("好，那先不唱了。")
            return

        if arg in {"列表", "歌单"}:
            titles = "、".join(str(s.get("title", "")) for s in songs)
            yield event.plain_result(f"现在会唱：{titles}")
            return

        if arg == "状态":
            if not session.get("song"):
                yield event.plain_result("还没开始唱。")
                return
            song = next((s for s in songs if s.get("slug") == session.get("song")), None)
            if not song:
                yield event.plain_result("进度丢了，发 /唱 重新开始吧。")
                return
            idx = int(session.get("index", 0))
            total = len(song.get("chunks", []))
            yield event.plain_result(f"正在唱《{song.get('title')}》：{min(idx + 1, total)}/{total} 段")
            return

        if arg:
            song = self._find_song(arg)
            if not song:
                titles = "、".join(str(s.get("title", "")) for s in songs)
                yield event.plain_result(f"没有找到《{arg}》。现在会唱：{titles}")
                return
            session = {"song": song.get("slug"), "index": 0}
            self.state.setdefault("sessions", {})[umo] = session
        else:
            song = next((s for s in songs if s.get("slug") == session.get("song")), None)
            if song is None:
                default = str(self.config.get("default_song", "") or "")
                song = self._find_song(default) if default else None
                if song is None:
                    song = random.choice(songs) if self.config.get("random_start", True) else songs[0]
                session = {"song": song.get("slug"), "index": 0}
                self.state.setdefault("sessions", {})[umo] = session

        chunks = song.get("chunks", [])
        idx = int(session.get("index", 0))
        if idx >= len(chunks):
            nxt = self._next_song(str(song.get("slug")))
            if not nxt:
                yield event.plain_result("唱完啦。")
                return
            song = nxt
            chunks = song.get("chunks", [])
            idx = 0
            session = {"song": song.get("slug"), "index": 0}
            self.state.setdefault("sessions", {})[umo] = session

        chunk = chunks[idx]
        path = self._chunk_path(str(song.get("slug")), str(chunk.get("file")))
        if not path.exists():
            logger.error("[aemeath_sing] 音频不存在: %s", path)
            yield event.plain_result("这一段音频找不到了。")
            return

        session["index"] = idx + 1
        self._save_state()
        logger.info(
            "[aemeath_sing] 发送 %s %s/%s",
            song.get("title"),
            idx + 1,
            len(chunks),
        )
        yield event.chain_result([Comp.Record(file=str(path), url=str(path))])

    @filter.command("唱")
    async def sing_cmd(self, event: AstrMessageEvent):
        async for result in self._sing(event):
            yield result

    @filter.command("唱歌")
    async def sing_cmd_alias(self, event: AstrMessageEvent):
        async for result in self._sing(event):
            yield result

    @filter.command("歌单")
    async def playlist_cmd(self, event: AstrMessageEvent):
        songs = self._songs()
        if not songs:
            yield event.plain_result("歌单还没准备好。")
            return
        titles = "、".join(str(s.get("title", "")) for s in songs)
        yield event.plain_result(f"现在会唱：{titles}")
