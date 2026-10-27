"""
爱弥斯 · 点唱插件（AstrBot）

三种触发：
    自然语言：我想听你唱歌 / 唱首歌 / 来一首 / 唱一段 ...
    主动唱：聊天中按概率突然想唱一句（有冷却和安静时段）
    命令：/唱、/唱 歌名、/唱 状态、/唱 停止

音频来自预先切好的歌曲片段，不调用 CosyVoice，也不需要 GPU。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.message.message_event_result import MessageChain

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # 兼容旧版本
    def get_astrbot_data_path():
        return "data"

TZ = ZoneInfo("Asia/Shanghai")

# 只匹配“请求唱歌”，避免“你唱得真好”这种误触
SING_PATTERNS = (
    "想听你唱",
    "想听你唱歌",
    "唱首歌",
    "唱个歌",
    "唱一首",
    "唱支歌",
    "唱一段",
    "唱两句",
    "唱几句",
    "给我唱",
    "唱给我听",
    "唱歌给我听",
    "来一首",
    "来首歌",
    "来一段",
    "你唱一",
    "你唱首",
    "你唱吧",
    "你唱嘛",
    "唱吧",
    "唱嘛",
    "想听歌",
    "唱一下",
)

AUTO_HINTS = ("难过", "不开心", "累了", "好累", "想你", "睡不着", "开心", "晚上", "晚安")


class AemeathSingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_aemeath_sing"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / "state.json"

        bundled = Path(__file__).resolve().parent
        self.manifest_file = bundled / "manifest.json"
        self.songs_dir = bundled / "songs"

        self.manifest = self._load_manifest()
        self.state = self._load_state()
        self._last_sing: dict[str, float] = {}
        self._last_auto: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()
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
            data.setdefault("sessions", {})
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

    # ---------- 选段 ----------

    def _advance(self, umo: str, query: str = "") -> tuple[dict, dict, int, int] | None:
        """取下一段并推进进度；返回 (song, chunk, 当前序号, 总段数)。"""
        songs = self._songs()
        if not songs:
            return None
        session = self._session(umo)

        if query:
            song = self._find_song(query)
            if not song:
                return None
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
                return None
            song = nxt
            chunks = song.get("chunks", [])
            idx = 0
            session = {"song": song.get("slug"), "index": 0}
            self.state.setdefault("sessions", {})[umo] = session

        chunk = chunks[idx]
        session["index"] = idx + 1
        self._save_state()
        return song, chunk, idx + 1, len(chunks)

    def _record(self, song: dict, chunk: dict, intro: str = "") -> object:
        path = self._chunk_path(str(song.get("slug")), str(chunk.get("file")))
        if not path.exists():
            raise FileNotFoundError(str(path))
        comps = []
        if intro:
            comps.append(Comp.Plain(intro))
        comps.append(Comp.Record(file=str(path), url=str(path)))
        return comps

    # ---------- 时间/概率 ----------

    @staticmethod
    def _parse_hhmm(value: str, default: str) -> dtime:
        try:
            h, m = str(value or default).split(":", 1)
            return dtime(int(h), int(m))
        except Exception:
            h, m = default.split(":", 1)
            return dtime(int(h), int(m))

    def _quiet_now(self) -> bool:
        start = self._parse_hhmm(str(self.config.get("auto_sing_quiet_start", "23:30")), "23:30")
        end = self._parse_hhmm(str(self.config.get("auto_sing_quiet_end", "08:00")), "08:00")
        now = datetime.now(TZ).time()
        if start <= end:
            return start <= now < end
        return now >= start or now < end

    def _cooling(self, umo: str, minutes: int) -> bool:
        last = self._last_sing.get(umo, 0.0)
        return time.time() - last < minutes * 60

    # ---------- 触发 ----------

    @staticmethod
    def _is_sing_request(text: str) -> bool:
        t = (text or "").strip()
        if not t or t.startswith("/"):
            return False
        return any(p in t for p in SING_PATTERNS)

    def _clean_command_arg(self, text: str) -> str:
        t = (text or "").strip()
        for prefix in (
            "/继续唱", "继续唱", "/再唱", "再唱", "/接着唱", "接着唱",
            "/唱歌", "/唱", "唱歌", "唱",
        ):
            if t.startswith(prefix):
                t = t[len(prefix):].strip()
                break
        if t in {"首歌", "一段", "一首", "歌", "继续", "接着"}:
            t = ""
        return t

    async def _maybe_auto(self, event: AstrMessageEvent) -> None:
        if not self.config.get("auto_sing_enabled", True):
            return
        if self._quiet_now():
            return
        umo = event.unified_msg_origin
        cooldown = int(self.config.get("auto_sing_cooldown_minutes", 60))
        if time.time() - self._last_auto.get(umo, 0.0) < cooldown * 60:
            return

        percent = float(self.config.get("auto_sing_percent", 3))
        text = event.message_str or ""
        if any(h in text for h in AUTO_HINTS):
            percent *= 2
        if random.random() * 100 >= percent:
            return

        self._last_auto[umo] = time.time()
        delay = max(1, int(self.config.get("auto_sing_delay_seconds", 5)))

        async def later():
            await asyncio.sleep(delay)
            if self._quiet_now():
                return
            got = self._advance(umo)
            if not got:
                return
            song, chunk, idx, total = got
            intro = str(self.config.get("auto_sing_intro", "突然想唱一句～"))
            try:
                await self.context.send_message(
                    umo,
                    MessageChain(self._record(song, chunk, intro)),
                )
                logger.info("[aemeath_sing] 主动唱 %s %s/%s", song.get("title"), idx, total)
            except Exception as exc:
                logger.error("[aemeath_sing] 主动唱失败: %s", exc)

        task = asyncio.create_task(later())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def natural_trigger(self, event: AstrMessageEvent):
        if not self.config.get("enabled", True):
            return
        text = (event.message_str or "").strip()
        if self._is_sing_request(text):
            event.stop_event()
            umo = event.unified_msg_origin
            if self._cooling(umo, int(self.config.get("cooldown_minutes", 2))):
                yield event.plain_result("刚唱过啦，等我缓一下嘛～")
                return
            got = self._advance(umo)
            if not got:
                yield event.plain_result("歌单还没准备好。")
                return
            song, chunk, idx, total = got
            self._last_sing[umo] = time.time()
            logger.info("[aemeath_sing] 自然语言点唱 %s %s/%s", song.get("title"), idx, total)
            try:
                yield event.chain_result(self._record(song, chunk, "好呀，那我唱几句～"))
            except Exception as exc:
                logger.error("[aemeath_sing] 发送失败: %s", exc)
                yield event.plain_result("这一段音频找不到了。")
            return

        # 不是点唱请求时，按概率决定她要不要主动唱
        if not text.startswith("/"):
            await self._maybe_auto(event)

    @filter.command("唱")
    async def sing_cmd(self, event: AstrMessageEvent):
        async for result in self._sing_command(event):
            yield result

    @filter.command("唱歌")
    async def sing_cmd_alias(self, event: AstrMessageEvent):
        async for result in self._sing_command(event):
            yield result

    async def _sing_command(self, event: AstrMessageEvent):
        songs = self._songs()
        if not songs:
            yield event.plain_result("歌单还没准备好。")
            return
        arg = self._clean_command_arg(event.message_str or "")
        umo = event.unified_msg_origin

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
            session = self._session(umo)
            song = next((s for s in songs if s.get("slug") == session.get("song")), None)
            if not song:
                yield event.plain_result("还没开始唱。")
                return
            idx = int(session.get("index", 0))
            total = len(song.get("chunks", []))
            yield event.plain_result(f"正在唱《{song.get('title')}》：{min(idx + 1, total)}/{total} 段")
            return

        got = self._advance(umo, arg)
        if not got:
            if arg:
                titles = "、".join(str(s.get("title", "")) for s in songs)
                yield event.plain_result(f"没有找到《{arg}》。现在会唱：{titles}")
            else:
                yield event.plain_result("唱完啦。")
            return
        song, chunk, idx, total = got
        self._last_sing[umo] = time.time()
        logger.info("[aemeath_sing] 命令点唱 %s %s/%s", song.get("title"), idx, total)
        try:
            yield event.chain_result(self._record(song, chunk))
        except Exception as exc:
            logger.error("[aemeath_sing] 发送失败: %s", exc)
            yield event.plain_result("这一段音频找不到了。")

    @filter.command("歌单")
    async def playlist_cmd(self, event: AstrMessageEvent):
        songs = self._songs()
        if not songs:
            yield event.plain_result("歌单还没准备好。")
            return
        titles = "、".join(str(s.get("title", "")) for s in songs)
        yield event.plain_result(f"现在会唱：{titles}")
