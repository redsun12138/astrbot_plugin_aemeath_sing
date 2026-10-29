"""
爱弥斯 · 点唱插件（AstrBot）

三种触发：
    自然语言：我想听你唱歌 / 唱首歌 / 来一首 / 唱一段 ...
    主动回唱：她发过主动消息后，用户回复时才可能唱；一条主动消息最多一次
    命令：/唱、/唱 歌名、/唱 状态、/唱 停止

音频来自预先切好的歌曲片段，不调用 CosyVoice，也不需要 GPU。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from pathlib import Path

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

# 用户求鼓励/奖励时，交给“她自己判断”而不是直接命令触发
ENCOURAGE_PATTERNS = (
    "鼓励我",
    "鼓励一下",
    "奖励我",
    "奖励一下",
    "夸夸我",
    "夸我一下",
    "给我打气",
    "安慰我",
    "哄哄我",
    "求鼓励",
    "求奖励",
)

# 她察觉到用户需要陪伴/鼓励时，可以自己唱
CARE_PATTERNS = (
    "难过",
    "不开心",
    "心情不好",
    "好累",
    "累死",
    "压力",
    "崩溃",
    "想哭",
    "失败",
    "被骂",
    "孤独",
    "失眠",
    "睡不着",
    "焦虑",
    "委屈",
    "需要鼓励",
    "求安慰",
    "奖励我",
    "夸夸我",
    "给我打气",
)

# 这些是“聊唱歌”，不是“想听她唱”
NON_SING_PATTERNS = (
    "唱歌比赛",
    "唱歌软件",
    "唱歌教程",
    "唱歌好听",
    "唱歌难听",
    "会唱歌吗",
    "唱过歌",
    "唱歌节目",
    "唱歌视频",
    "唱歌的人",
)

SING_MARKER_PROMPT = """
【唱歌能力】
你可以在想唱歌的时候，在回复的最后单独加一个 [sing] 标记，系统会在文字回复后播放一段爱弥斯的歌。
只在真的想唱、想鼓励或奖励对方、或者对方难过/需要安慰时使用。
不要每句话都加，不要连续两条回复都加；不想唱就不要加，也不要解释这个标记。
""".strip()


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
        self._tasks: set[asyncio.Task] = set()
        self.proactive_state_file = (
            self.data_dir.parent / "astrbot_plugin_proactive_care" / "state.json"
        )
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

    # ---------- 她自己想唱 ----------

    def _schedule_self_sing(
        self,
        umo: str,
        intro: str | None = None,
        delay: int | None = None,
        reason: str = "[sing]",
    ) -> None:
        if not self.config.get("self_sing_enabled", True):
            return
        if self._cooling(umo, int(self.config.get("self_sing_cooldown_minutes", 20))):
            return
        self._last_sing[umo] = time.time()
        if delay is None:
            delay = max(1, int(self.config.get("self_sing_delay_seconds", 2)))
        if intro is None:
            intro = str(self.config.get("self_sing_intro", "那我唱一段～"))

        async def later():
            await asyncio.sleep(delay)
            got = self._advance(umo)
            if not got:
                return
            song, chunk, idx, total = got
            try:
                await self.context.send_message(
                    umo,
                    MessageChain(self._record(song, chunk, intro)),
                )
                logger.info("[aemeath_sing] %s 自主唱 %s %s/%s", reason, song.get("title"), idx, total)
            except Exception as exc:
                logger.error("[aemeath_sing] 自主唱失败: %s", exc)

        task = asyncio.create_task(later())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _maybe_care_reply(self, event: AstrMessageEvent, reply_text: str) -> None:
        """安慰语境下，如果她自己的回复里说要唱/提到歌，就自动接一段。"""
        if not self.config.get("care_sing_enabled", True):
            return
        if not self._is_care(event.message_str or ""):
            return
        if not re.search(r"唱|歌", reply_text or ""):
            return
        if re.search(r"不会唱|不想唱|别唱|唱不了|跑调|唱歌比赛", reply_text or ""):
            return
        if getattr(event, "_aemeath_sing_scheduled", False):
            return
        umo = event.unified_msg_origin
        if self._cooling(umo, int(self.config.get("care_sing_cooldown_minutes", 20))):
            return
        percent = float(self.config.get("care_sing_percent", 100))
        if random.random() * 100 >= percent:
            return
        event._aemeath_sing_scheduled = True
        self._schedule_self_sing(
            umo,
            intro="",  # 她回复里已经说了要唱，不再重复开场白
            delay=max(1, int(self.config.get("care_sing_delay_seconds", 2))),
            reason="[care-reply]",
        )

    @filter.on_llm_request()
    async def inject_sing_prompt(self, event: AstrMessageEvent, req) -> None:
        if not self.config.get("enabled", True):
            return
        if not self.config.get("self_sing_enabled", True):
            return
        prompt = getattr(req, "system_prompt", "") or ""
        if "【唱歌能力】" not in prompt:
            req.system_prompt = prompt + "\n\n" + SING_MARKER_PROMPT

    @filter.on_llm_response()
    async def detect_sing_marker(self, event: AstrMessageEvent, response) -> None:
        if not self.config.get("enabled", True):
            return
        if not self.config.get("self_sing_enabled", True):
            return
        text = getattr(response, "completion_text", "") or ""
        if re.search(r"\[sing\]", text, re.IGNORECASE):
            response.completion_text = re.sub(
                r"\s*\[sing\]\s*", "", text, flags=re.IGNORECASE
            ).strip()
            if getattr(event, "_aemeath_sing_scheduled", False):
                return
            event._aemeath_sing_scheduled = True
            self._schedule_self_sing(event.unified_msg_origin)
            return

        # 没有 [sing] 时：安慰语境 + 她自己的回复里提到唱/歌，也接一段
        self._maybe_care_reply(event, text)

    @filter.on_decorating_result()
    async def strip_sing_marker(self, event: AstrMessageEvent) -> None:
        """兜底：如果模型把 [sing] 留到了最终消息链里，也移除并触发。"""
        try:
            result = event.get_result()
            chain = getattr(result, "chain", None) or []
            raw = "".join(
                (getattr(seg, "text", "") or "")
                for seg in chain
                if isinstance(seg, Comp.Plain)
            )
            if re.search(r"\[sing\]", raw, re.IGNORECASE):
                for seg in chain:
                    if isinstance(seg, Comp.Plain) and seg.text:
                        seg.text = re.sub(
                            r"\s*\[sing\]\s*", "", seg.text, flags=re.IGNORECASE
                        ).strip()
                if not getattr(event, "_aemeath_sing_scheduled", False):
                    event._aemeath_sing_scheduled = True
                    self._schedule_self_sing(event.unified_msg_origin)
            else:
                self._maybe_care_reply(event, raw)
        except Exception as exc:
            logger.error("[aemeath_sing] 处理 [sing] 标记失败: %s", exc)

    # ---------- 主动消息回唱 ----------

    def _proactive_ts(self, umo: str) -> float:
        """读取主动关心插件记录的“最近一次主动消息”时间。"""
        try:
            data = json.loads(self.proactive_state_file.read_text(encoding="utf-8"))
            st = data.get("sessions", {}).get(umo, {})
            return float(st.get("last_proactive_at") or 0.0)
        except Exception:
            return 0.0

    def _mark_proactive_seen(self, umo: str, ts: float) -> None:
        if ts:
            self._session(umo)["last_proactive_seen"] = ts
            self._save_state()

    def _cooling(self, umo: str, minutes: int) -> bool:
        last = self._last_sing.get(umo, 0.0)
        return time.time() - last < minutes * 60

    # ---------- 触发 ----------

    @staticmethod
    def _is_sing_request(text: str) -> bool:
        t = (text or "").strip()
        if not t or t.startswith("/"):
            return False
        if any(p in t for p in NON_SING_PATTERNS):
            return False
        # 宽松规则：同时出现“唱”和“歌”就当想听歌
        if "唱" in t and "歌" in t:
            return True
        return any(p in t for p in SING_PATTERNS)

    @staticmethod
    def _is_care(text: str) -> bool:
        t = (text or "").strip()
        return any(p in t for p in CARE_PATTERNS)

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

    async def _maybe_sing_after_proactive(self, event: AstrMessageEvent) -> None:
        """只在她发过主动消息、且用户回复之后才可能唱；一条主动消息最多一次。"""
        if not self.config.get("proactive_reply_enabled", True):
            return
        umo = event.unified_msg_origin
        proactive_ts = self._proactive_ts(umo)
        if proactive_ts <= 0:
            return

        window = int(self.config.get("proactive_reply_window_minutes", 30))
        if time.time() - proactive_ts > window * 60:
            return

        session = self._session(umo)
        if float(session.get("last_proactive_seen") or 0) >= proactive_ts:
            return

        # 一条主动消息只给一次机会，避免用户连发几条时反复抽签
        self._mark_proactive_seen(umo, proactive_ts)
        percent = float(self.config.get("proactive_reply_percent", 0))
        if random.random() * 100 >= percent:
            return

        delay = max(1, int(self.config.get("proactive_reply_delay_seconds", 4)))

        async def later():
            await asyncio.sleep(delay)
            got = self._advance(umo)
            if not got:
                return
            song, chunk, idx, total = got
            intro = str(self.config.get("proactive_reply_intro", "突然想唱一句～"))
            try:
                await self.context.send_message(
                    umo,
                    MessageChain(self._record(song, chunk, intro)),
                )
                logger.info(
                    "[aemeath_sing] 回复主动消息后唱 %s %s/%s",
                    song.get("title"),
                    idx,
                    total,
                )
            except Exception as exc:
                logger.error("[aemeath_sing] 主动回唱失败: %s", exc)

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
            self._mark_proactive_seen(umo, self._proactive_ts(umo))
            logger.info("[aemeath_sing] 自然语言点唱 %s %s/%s", song.get("title"), idx, total)
            try:
                intro = "好呀，那我唱几句～"
                yield event.chain_result(self._record(song, chunk, intro))
            except Exception as exc:
                logger.error("[aemeath_sing] 发送失败: %s", exc)
                yield event.plain_result("这一段音频找不到了。")
            return

        # 不是点唱请求时，再看是否在回复主动消息
        if not text.startswith("/"):
            await self._maybe_sing_after_proactive(event)

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
        self._mark_proactive_seen(umo, self._proactive_ts(umo))
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
