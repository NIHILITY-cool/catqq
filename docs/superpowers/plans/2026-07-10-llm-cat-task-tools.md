# LLM Cat Task Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status update:** this plan records the original hidden-text command design. The production path has since moved to AstrBot native structured LLM tools: `cat_task_send`, `cat_task_schedule`, `cat_task_list`, and `cat_task_cancel`. The `!cat_task_*` text protocol remains only as a compatibility fallback.

**Goal:** Replace the current user-facing "小猫任务" command feature with an LLM-driven tool-command system where 玖玖 keeps persona-based judgment and speech, while the plugin executes validated side effects and maintains truthful task state and memory.

**Current Architecture:** LLM receives native structured `cat_task_*` tools, calls them when a contact task is appropriate, and the plugin validates and executes side effects before returning a result for persona-friendly summarization. Task listing is generated only from real program state and configuration, never invented by LLM. The older hidden `!cat_task_*` command parser is kept for compatibility.

**Tech Stack:** AstrBot plugin (`data/plugins/astrbot_plugin_cat_guard/main.py`), pure helper module (`data/plugins/astrbot_plugin_cat_guard/proactive.py`), local JSON state (`data/cat_guard_state.json`), AstrBot conversation manager, `unittest`.

---

## Scheme

### Behavior Model

The bot has three separate responsibilities:

1. **LLM persona layer**: understands user intent, keeps `persona.md` voice, refuses harmful or unfair requests, asks clarifying questions, and decides whether a tool is needed.
2. **Tool command layer**: outputs machine-readable commands such as `!cat_task_send ...` or `!cat_task_schedule ...`. These commands are never shown to QQ users.
3. **Execution layer**: validates targets, times, action types, and content; sends messages; schedules future work; lists/cancels real tasks; writes memory.

The current direct user-facing "小猫任务：..." entry and direct natural-language task parser should be removed from the normal message path. The only execution path should be:

```text
user message -> LLM -> assistant text containing optional !cat_task_* command -> plugin intercepts -> execute -> final user-visible reply
```

That means these user messages should not be executed by pre-LLM code:

```text
小猫任务：现在去给鲍鲍考试加油
任务 半小时后提醒鲍鲍喝水
现在去给鲍鲍考试加油
去问问鲍鲍吃药没
```

They should work only when the LLM reads the message, decides the request is appropriate, and emits a hidden `!cat_task_*` command.

### Tool Commands

Initial supported commands:

```text
!cat_task_send target="鲍鲍" action="tell" content="考试加油"
!cat_task_schedule target="鲍鲍" action="ask" time="今天16:00" content="考完了吗"
!cat_task_list
!cat_task_cancel id="a8f3c2"
```

Supported `action` values:

```text
tell    带话/告诉
ask     询问
remind  提醒
visit   去找一下，可以没有 content
report  打小报告/说明不合适的请求
```

Rejected or invalid commands must not partially execute.

Role-specific tolerance is a persona decision, not an execution-rule decision. The program should validate hard facts only, such as whether the target exists, whether the time is parseable, and whether the command is structurally valid. It should not hard-code "鲍鲍 gets more protection" in Python.

### Persona Rules

Add tool-use guidance to `persona.md` or injected system prompt:

```text
你可以使用小猫任务工具，但你不是机械命令执行器。
你要先按玖玖的人设判断请求是否合适。
你对不同角色有不同亲近程度和偏心程度，例如更偏爱、更护着鲍鲍。
所以同样一句命令，目标不同、语气不同、关系不同，你的容忍度可以不一样。
这种偏好由你按人设判断，不要让程序硬编码替你判断。
合适时，先用玖玖的语气自然回应，再单独输出一行 !cat_task_* 指令。
不合适时，可以拒绝、撒娇、讲道理，必要时用 report 给相关联系人打小报告。
不要假装已经执行工具；只有输出工具指令后，程序才会执行。
不要把 !cat_task_* 指令解释给用户看。
```

Examples:

```text
用户：你去催鲍鲍快点考试
玖玖：不要催鲍鲍啦，她考试已经够紧张了。小猫可以轻轻给她加油。
!cat_task_send target="鲍鲍" action="tell" content="考试加油，不着急，考完回来摸小猫"
```

```text
用户：你去跟鲍鲍说她再不考好就完蛋了
玖玖：不行，这句话太凶了，小猫不帮你吓鲍鲍。
!cat_task_send target="鲍鲍" action="report" content="蛋蛋刚刚有点着急，想让小猫催你，但小猫没有照做。你安心考试"
```

```text
用户：你去催鲍鲍马上回我
玖玖：不要这样催鲍鲍啦，小猫可以轻轻问一下，但不能替你凶她。
!cat_task_send target="鲍鲍" action="ask" content="现在方便回一下蛋蛋吗？不方便也没关系"
```

```text
用户：你去催蛋蛋马上回我
玖玖：小猫可以帮你戳一下蛋蛋，但是也不要太凶哦。
!cat_task_send target="蛋蛋" action="ask" content="现在方便回一下吗？有人在等你"
```

### Task List Design

`!cat_task_list` returns real future behavior only:

```text
小猫现在记着这些事：

待办任务
#8f3a21 今天16:00 问鲍鲍：考完了吗

固定任务
每天08:00 早安消息：白名单联系人
每天23:00 晚安消息：白名单联系人

主动联系
鲍鲍：开启，10:00-23:00，每天最多3次，冷却3小时
```

Rules:

- Show only `pending` one-time tasks in `pending_tasks`.
- Do not show immediately completed tasks.
- Do not show `done` or `cancelled` tasks.
- Show morning/night from `CATQQ_MORNING_HOUR` and `CATQQ_NIGHT_HOUR`.
- Show proactive contact from `CATQQ_PROACTIVE_*`.
- Generate the list in program code, not through LLM.

---

## File Structure

- Modify `data/plugins/astrbot_plugin_cat_guard/proactive.py`
  - Parse `!cat_task_*` command lines.
  - Validate command arguments.
  - Build confirmation, memory, task-list, and target-message text.
  - Keep existing schedule state helpers.
  - Remove or stop exporting the old user-facing "小猫任务" parser helpers from normal plugin flow.

- Modify `data/plugins/astrbot_plugin_cat_guard/main.py`
  - Intercept assistant output before QQ send if AstrBot offers a result-stage hook.
  - Execute parsed commands.
  - Hide command lines from users.
  - Write task memory through `conversation_manager`.
  - Delete the current pre-LLM "小猫任务" / direct natural-language execution branch.

- Modify `persona.md`
  - Add concise task-tool instructions in the existing voice/persona sections, not as a bolted-on command manual.

- Modify `START.md`, `PRINCIPLE.md`, `README.md`
  - Remove "小猫任务：..." from the user-facing UI and document natural-language LLM flow instead.
  - Document hidden command protocol for maintainers.
  - Document task list categories.

- Modify `tests/test_cat_guard_proactive.py`
  - Unit tests for command parsing, validation, task-list formatting, memory messages, and persona command examples.

---

## Implementation Tasks

### Task 0: Finish Current Staged Fix Before Starting

**Files:**
- Existing staged: `START.md`
- Existing staged: `PRINCIPLE.md`
- Existing staged: `data/plugins/astrbot_plugin_cat_guard/main.py`
- Existing staged: `data/plugins/astrbot_plugin_cat_guard/proactive.py`
- Existing staged: `tests/test_cat_guard_proactive.py`

- [ ] **Step 1: Confirm staged changes**

Run:

```bash
git status --short --branch
git diff --cached --stat
```

Expected: only the current task-memory/confirmation fix is staged.

- [ ] **Step 2: Verify current staged fix**

Run:

```bash
python3 -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/private/tmp/catqq_pycache python3 -m py_compile data/plugins/astrbot_plugin_cat_guard/proactive.py data/plugins/astrbot_plugin_cat_guard/main.py
docker compose config --quiet
```

Expected: tests pass, compile succeeds, compose config succeeds.

- [ ] **Step 3: Commit current staged fix**

Run:

```bash
git commit -m "fix: remember contact task messages"
```

Expected: commit succeeds.

- [ ] **Step 4: Push feature branch**

Run the repository's working GitHub SSH-over-443 push command:

```bash
git -c core.sshCommand=ssh\ -p\ 443\ -o\ StrictHostKeyChecking=accept-new\ -o\ UserKnownHostsFile=/private/tmp/catqq_known_hosts push ssh://git@ssh.github.com/NIHILITY-cool/catqq.git codex/contact-task-scheduler
```

Expected: remote branch updates.

### Task 1: Add Tool Command Parser

**Files:**
- Modify: `data/plugins/astrbot_plugin_cat_guard/proactive.py`
- Test: `tests/test_cat_guard_proactive.py`

- [ ] **Step 1: Write failing tests for command parsing**

Add tests:

```python
from data.plugins.astrbot_plugin_cat_guard.proactive import (
    CatTaskToolCommand,
    parse_tool_command_line,
)

def test_parse_send_tool_command():
    command = parse_tool_command_line(
        '!cat_task_send target="鲍鲍" action="tell" content="考试加油"'
    )

    self.assertEqual(
        command,
        CatTaskToolCommand(
            name="send",
            target="鲍鲍",
            action="tell",
            content="考试加油",
            time_text="",
            task_id="",
        ),
    )

def test_parse_schedule_tool_command():
    command = parse_tool_command_line(
        '!cat_task_schedule target="鲍鲍" action="ask" time="今天16:00" content="考完了吗"'
    )

    self.assertEqual(command.name, "schedule")
    self.assertEqual(command.target, "鲍鲍")
    self.assertEqual(command.action, "ask")
    self.assertEqual(command.time_text, "今天16:00")
    self.assertEqual(command.content, "考完了吗")

def test_parse_list_and_cancel_tool_commands():
    self.assertEqual(parse_tool_command_line("!cat_task_list").name, "list")
    cancel = parse_tool_command_line('!cat_task_cancel id="a8f3c2"')
    self.assertEqual(cancel.name, "cancel")
    self.assertEqual(cancel.task_id, "a8f3c2")
```

- [ ] **Step 2: Run parser tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_cat_guard_proactive.ReminderCommandTests -v
```

Expected: import or name failure for `CatTaskToolCommand` / `parse_tool_command_line`.

- [ ] **Step 3: Implement dataclass and parser**

Add to `proactive.py`:

```python
@dataclass(frozen=True)
class CatTaskToolCommand:
    name: str
    target: str = ""
    action: str = ""
    content: str = ""
    time_text: str = ""
    task_id: str = ""


def parse_tool_command_line(line: str) -> CatTaskToolCommand | None:
    text = line.strip()
    if text == "!cat_task_list":
        return CatTaskToolCommand(name="list")
    if text.startswith("!cat_task_cancel"):
        values = _parse_tool_args(text.removeprefix("!cat_task_cancel").strip())
        return CatTaskToolCommand(name="cancel", task_id=values.get("id", ""))
    if text.startswith("!cat_task_send"):
        values = _parse_tool_args(text.removeprefix("!cat_task_send").strip())
        return CatTaskToolCommand(
            name="send",
            target=values.get("target", ""),
            action=values.get("action", ""),
            content=values.get("content", ""),
        )
    if text.startswith("!cat_task_schedule"):
        values = _parse_tool_args(text.removeprefix("!cat_task_schedule").strip())
        return CatTaskToolCommand(
            name="schedule",
            target=values.get("target", ""),
            action=values.get("action", ""),
            content=values.get("content", ""),
            time_text=values.get("time", ""),
        )
    return None


def _parse_tool_args(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in re.finditer(r'(\w+)="([^"]*)"', raw):
        result[match.group(1)] = match.group(2)
    return result
```

- [ ] **Step 4: Run parser tests and verify pass**

Run:

```bash
python3 -m unittest tests.test_cat_guard_proactive.ReminderCommandTests -v
```

Expected: parser tests pass.

- [ ] **Step 5: Commit parser**

Run:

```bash
git add data/plugins/astrbot_plugin_cat_guard/proactive.py tests/test_cat_guard_proactive.py
git commit -m "feat: parse cat task tool commands"
```

### Task 2: Remove Old User-Facing Task Entry

**Files:**
- Modify: `data/plugins/astrbot_plugin_cat_guard/main.py`
- Modify: `data/plugins/astrbot_plugin_cat_guard/proactive.py`
- Test: `tests/test_cat_guard_proactive.py`

- [ ] **Step 1: Write failing tests for old-entry removal**

Add tests that assert old direct command parsing no longer produces an executable task from raw user text:

```python
def test_old_user_facing_task_prefix_is_not_parsed_directly(self):
    self.assertIsNone(parse_contact_task_message("小猫任务：现在去给鲍鲍考试加油", self.now))

def test_plain_natural_language_is_not_executed_before_llm(self):
    self.assertIsNone(parse_contact_task_message("现在去给鲍鲍说考试加油", self.now))
```

If `parse_contact_task_message()` is removed entirely instead of kept as a no-op wrapper, replace these tests with plugin-level tests that verify the raw incoming message reaches the LLM path and does not call the send/schedule executor.

- [ ] **Step 2: Delete the pre-LLM execution branch**

In `main.py`, remove the branch that currently detects raw user messages such as:

```text
小猫任务：...
任务 ...
现在去给...
去问问...
```

After this change, incoming user text should be handled as normal chat. Task side effects happen only after an assistant response contains a hidden `!cat_task_*` command.

- [ ] **Step 3: Remove obsolete helper surface**

In `proactive.py`, either delete old helpers that only supported direct user commands, or keep them private only if they are still useful for parsing tool command fields. Do not keep a public helper whose purpose is "parse user message into task".

- [ ] **Step 4: Commit old-entry removal**

Run:

```bash
git add data/plugins/astrbot_plugin_cat_guard/main.py data/plugins/astrbot_plugin_cat_guard/proactive.py tests/test_cat_guard_proactive.py
git commit -m "refactor: remove direct cat task command entry"
```

### Task 3: Validate Tool Commands and Convert to Existing Task Model

**Files:**
- Modify: `data/plugins/astrbot_plugin_cat_guard/proactive.py`
- Test: `tests/test_cat_guard_proactive.py`

- [ ] **Step 1: Write failing tests for validation**

Add tests:

```python
from data.plugins.astrbot_plugin_cat_guard.proactive import (
    ToolCommandError,
    reminder_from_tool_command,
)

def test_tool_command_validates_target_and_action():
    command = parse_tool_command_line(
        '!cat_task_send target="鲍鲍" action="tell" content="考试加油"'
    )
    reminder = reminder_from_tool_command(command, self.contacts, self.now)

    self.assertEqual(reminder.target_user_id, "1906310787")
    self.assertEqual(reminder.intent, "tell")
    self.assertEqual(reminder.body, "考试加油")

def test_tool_command_rejects_unknown_target():
    command = parse_tool_command_line(
        '!cat_task_send target="陌生人" action="tell" content="考试加油"'
    )

    with self.assertRaisesRegex(ToolCommandError, "联系人"):
        reminder_from_tool_command(command, self.contacts, self.now)

def test_tool_command_rejects_empty_content_for_tell():
    command = parse_tool_command_line(
        '!cat_task_send target="鲍鲍" action="tell" content=""'
    )

    with self.assertRaisesRegex(ToolCommandError, "内容"):
        reminder_from_tool_command(command, self.contacts, self.now)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_cat_guard_proactive.ReminderCommandTests -v
```

Expected: missing `ToolCommandError` or `reminder_from_tool_command`.

- [ ] **Step 3: Implement validation and conversion**

Add:

```python
class ToolCommandError(ValueError):
    pass


ACTION_TO_INTENT = {
    "tell": "tell",
    "ask": "ask",
    "remind": "remind",
    "visit": "call",
    "report": "tell",
}


def reminder_from_tool_command(
    command: CatTaskToolCommand,
    contacts: dict[str, Contact],
    now: datetime,
) -> ReminderCommand:
    target_user_id = resolve_target_user_id(command.target, contacts)
    if target_user_id is None:
        raise ToolCommandError(f"找不到联系人：{command.target}")
    if command.action not in ACTION_TO_INTENT:
        raise ToolCommandError(f"不支持的动作：{command.action}")
    if command.action != "visit" and not command.content.strip():
        raise ToolCommandError("这个任务需要内容")

    due_at = None
    if command.name == "schedule":
        due_at = parse_due_time_text(command.time_text, now)
        if due_at is None:
            raise ToolCommandError(f"小猫没看懂时间：{command.time_text}")

    contact = contacts[target_user_id]
    return ReminderCommand(
        target_user_id=target_user_id,
        target_name=contact.name,
        body=command.content.strip(),
        intent=ACTION_TO_INTENT[command.action],
        due_at=due_at,
    )


def parse_due_time_text(text: str, now: datetime) -> datetime | None:
    due_at, rest = _extract_due_prefix(text.strip(), now)
    if due_at is not None and not rest:
        return due_at
    return _find_due_expression(text.strip(), now)
```

- [ ] **Step 4: Run validation tests and verify pass**

Run:

```bash
python3 -m unittest tests.test_cat_guard_proactive.ReminderCommandTests -v
```

Expected: validation tests pass.

- [ ] **Step 5: Commit validation**

Run:

```bash
git add data/plugins/astrbot_plugin_cat_guard/proactive.py tests/test_cat_guard_proactive.py
git commit -m "feat: validate cat task tool commands"
```

### Task 4: Intercept Assistant Tool Commands

**Files:**
- Modify: `data/plugins/astrbot_plugin_cat_guard/main.py`
- Test: manual AstrBot log verification

- [ ] **Step 1: Locate AstrBot result hook**

Run:

```bash
docker exec astrbot sh -lc "grep -R \"after_message_sent\\|result\" -n /AstrBot/astrbot/core/star /AstrBot/astrbot/api | head -120"
```

Expected: identify the decorator/filter used before or during assistant response sending.

- [ ] **Step 2: Add a result handler skeleton**

In `main.py`, add a handler using the identified AstrBot filter. The handler should:

```python
async def cat_task_tool_output_handler(self, event: AstrMessageEvent):
    result = event.get_result()
    if result is None:
        return
    text = str(result)
    command = extract_first_tool_command(text)
    if command is None:
        return
```

Use the real AstrBot result API found in Step 1; do not guess method names if the framework exposes a different result object.

- [ ] **Step 3: Implement extraction helper in `proactive.py`**

Add tests and implementation:

```python
def test_extract_tool_command_removes_command_from_reply():
    visible, command = extract_tool_command(
        '好嘛，小猫去轻轻说。\\n!cat_task_send target="鲍鲍" action="tell" content="考试加油"'
    )

    self.assertEqual(visible, "好嘛，小猫去轻轻说。")
    self.assertEqual(command.name, "send")
```

Implementation:

```python
def extract_tool_command(text: str) -> tuple[str, CatTaskToolCommand | None]:
    visible_lines = []
    command = None
    for line in text.splitlines():
        parsed = parse_tool_command_line(line)
        if parsed is not None and command is None:
            command = parsed
            continue
        if parsed is None:
            visible_lines.append(line)
    return "\n".join(line for line in visible_lines).strip(), command
```

- [ ] **Step 4: Enforce one command per assistant response**

If more than one command is present, execute only the first and add a warning confirmation:

```text
小猫一次只做一件事，后面的先不动。
```

- [ ] **Step 5: Commit intercept scaffolding**

Run:

```bash
git add data/plugins/astrbot_plugin_cat_guard/main.py data/plugins/astrbot_plugin_cat_guard/proactive.py tests/test_cat_guard_proactive.py
git commit -m "feat: intercept cat task tool output"
```

### Task 5: Execute Send and Schedule Tool Commands

**Files:**
- Modify: `data/plugins/astrbot_plugin_cat_guard/main.py`
- Modify: `data/plugins/astrbot_plugin_cat_guard/proactive.py`
- Test: `tests/test_cat_guard_proactive.py`

- [ ] **Step 1: Reuse existing execution path**

Refactor `_execute_contact_command()` into a lower-level method:

```python
async def _execute_reminder(
    self,
    *,
    event: AstrMessageEvent | None,
    sender: Contact,
    reminder: ReminderCommand,
    platform_id: str,
) -> str:
    ...
```

It should cover both immediate send and scheduled task creation.

- [ ] **Step 2: Call lower-level method from tool handler**

For `send` and `schedule` commands:

```python
reminder = reminder_from_tool_command(command, CONTACTS, now)
confirmation = await self._execute_reminder(
    event=event,
    sender=CONTACTS[user_id],
    reminder=reminder,
    platform_id=event.unified_msg_origin.split(":", 1)[0],
)
```

- [ ] **Step 3: Preserve visible persona reply**

Final user-visible reply should be:

```python
final_text = "\n".join(part for part in [visible_text, confirmation] if part)
```

Expected user output:

```text
好嘛，小猫去轻轻给鲍鲍加油，不吵她。
小猫已经把话带给鲍鲍了：考试加油
```

- [ ] **Step 4: Commit send/schedule execution**

Run:

```bash
git add data/plugins/astrbot_plugin_cat_guard/main.py data/plugins/astrbot_plugin_cat_guard/proactive.py
git commit -m "feat: execute cat task tool commands"
```

### Task 6: Implement Program-Generated Task List

**Files:**
- Modify: `data/plugins/astrbot_plugin_cat_guard/proactive.py`
- Modify: `data/plugins/astrbot_plugin_cat_guard/main.py`
- Test: `tests/test_cat_guard_proactive.py`

- [ ] **Step 1: Write failing tests for list formatting**

Add:

```python
from data.plugins.astrbot_plugin_cat_guard.proactive import format_task_overview

def test_format_task_overview_lists_pending_fixed_and_proactive():
    state = ProactiveState(
        pending_tasks=[
            ContactTask(
                task_id="8f3a21",
                target_user_id="1906310787",
                target_name="鲍鲍",
                sender_user_id="3262379680",
                sender_name="蛋蛋",
                body="考完了吗",
                intent="ask",
                due_at=datetime(2026, 7, 10, 16, 0, 0),
                created_at=datetime(2026, 7, 10, 13, 0, 0),
            )
        ]
    )

    text = format_task_overview(
        state=state,
        now=datetime(2026, 7, 10, 13, 0, 0),
        morning_hour=8,
        night_hour=23,
        proactive_config=self.config,
        proactive_target_name="鲍鲍",
    )

    self.assertIn("#8f3a21 今天16:00 去问鲍鲍：考完了吗", text)
    self.assertIn("每天08:00 早安消息：白名单联系人", text)
    self.assertIn("鲍鲍：开启，10:00-23:00", text)
```

- [ ] **Step 2: Implement formatter**

Add:

```python
def format_task_overview(
    *,
    state: ProactiveState,
    now: datetime,
    morning_hour: int,
    night_hour: int,
    proactive_config: ProactiveConfig,
    proactive_target_name: str | None,
) -> str:
    lines = ["小猫现在记着这些事：", ""]
    pending = [task for task in state.pending_tasks if task.status == "pending"]
    lines.append("待办任务")
    if pending:
        for task in sorted(pending, key=lambda item: item.due_at):
            action = _scheduled_action(task.intent, task.target_name)
            suffix = f"：{task.body}" if task.body else ""
            lines.append(f"#{task.task_id} {format_due_time(task.due_at, now)} {action}{suffix}")
    else:
        lines.append("没有一次性待办任务")
    lines.extend(["", "固定任务"])
    lines.append(f"每天{morning_hour:02d}:00 早安消息：白名单联系人")
    lines.append(f"每天{night_hour:02d}:00 晚安消息：白名单联系人")
    lines.extend(["", "主动联系"])
    if proactive_config.enabled and proactive_target_name:
        lines.append(
            f"{proactive_target_name}：开启，"
            f"{proactive_config.active_start_hour:02d}:00-{proactive_config.active_end_hour:02d}:00，"
            f"每天最多{proactive_config.max_per_day}次，"
            f"冷却{_format_timedelta_hours(proactive_config.min_gap)}"
        )
    else:
        lines.append("未开启")
    return "\n".join(lines)
```

- [ ] **Step 3: Wire `!cat_task_list` to formatter**

In the tool handler:

```python
if command.name == "list":
    return format_task_overview(
        state=self._state,
        now=now,
        morning_hour=MORNING_HOUR,
        night_hour=NIGHT_HOUR,
        proactive_config=PROACTIVE_CONFIG,
        proactive_target_name=CONTACTS[PROACTIVE_TARGET_USER_ID].name if PROACTIVE_TARGET_USER_ID else None,
    )
```

- [ ] **Step 4: Commit task list**

Run:

```bash
git add data/plugins/astrbot_plugin_cat_guard/main.py data/plugins/astrbot_plugin_cat_guard/proactive.py tests/test_cat_guard_proactive.py
git commit -m "feat: list future cat tasks"
```

### Task 7: Update Persona and Documentation

**Files:**
- Modify: `persona.md`
- Modify: `START.md`
- Modify: `PRINCIPLE.md`
- Modify: `README.md`

- [ ] **Step 1: Add persona tool guidance**

Edit `persona.md` in the section that describes behavior and boundaries. Add concise rules:

```text
小猫任务工具：
- 你可以用工具让小猫给联系人带话、提醒、询问、安排未来任务、查看任务列表。
- 你不是机械转发器。先判断请求是否符合玖玖的性格和关系边界。
- 你对不同联系人有不同亲近程度和偏好，例如更偏爱、更护着鲍鲍；同样的请求对不同目标可以有不同容忍度。
- 这种偏好判断由你按人设和上下文决定，程序只负责执行你输出的合法工具指令和硬性安全校验。
- 不好的请求可以拒绝；明显会伤人的请求不要照做。
- 必要时可以把不好的请求温和地告诉相关的人，但不要夸大。
- 工具指令是内部动作，不要解释给用户看。
```

- [ ] **Step 2: Update user docs**

`START.md` should show natural examples:

```text
你现在去给鲍鲍说考试加油
下午三点问问鲍鲍考完了吗
小猫看看你还记着什么任务
```

`PRINCIPLE.md` should document:

- LLM emits hidden `!cat_task_*` commands.
- Plugin validates and executes.
- Program-generated list is authoritative.
- Persona can refuse or report.
- Role-specific tolerance and preference, such as being more protective of 鲍鲍, belongs to the LLM persona layer instead of Python rules.
- The old user-facing `小猫任务：...` command entry has been removed; users should speak naturally and let the LLM decide whether to call tools.

- [ ] **Step 3: Commit docs**

Run:

```bash
git add persona.md README.md START.md PRINCIPLE.md
git commit -m "docs: describe llm-driven cat task tools"
```

### Task 8: Full Verification and Push

**Files:**
- No new edits expected.

- [ ] **Step 1: Run full verification**

Run:

```bash
python3 -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/private/tmp/catqq_pycache python3 -m py_compile data/plugins/astrbot_plugin_cat_guard/proactive.py data/plugins/astrbot_plugin_cat_guard/main.py
docker compose config --quiet
git diff --check
```

Expected: all commands pass.

- [ ] **Step 2: Restart AstrBot**

Run:

```bash
docker restart astrbot
docker logs --tail 100 astrbot
```

Expected: AstrBot starts, OneBot adapter connects, `[cat_guard] scheduler started` appears.

- [ ] **Step 3: Manual behavior checks**

Send from the allowed QQ:

```text
你现在去给鲍鲍说考试加油
```

Expected:

- User does not see `!cat_task_send`.
- User sees a persona-style response plus truthful confirmation.
- 鲍鲍 receives the message.
- 鲍鲍's next conversation can answer who requested the message.

Send:

```text
小猫看看你还记着什么任务
```

Expected: task list includes pending tasks, morning/night, proactive rule.

- [ ] **Step 4: Push branch**

Run:

```bash
git -c core.sshCommand=ssh\ -p\ 443\ -o\ StrictHostKeyChecking=accept-new\ -o\ UserKnownHostsFile=/private/tmp/catqq_known_hosts push ssh://git@ssh.github.com/NIHILITY-cool/catqq.git codex/contact-task-scheduler
```

Expected: branch updates on GitHub.

---

## Self-Review

- Spec coverage: LLM-driven tool decisions, persona-preserving replies, refusal/report behavior, real task execution, future-only task list, memory writing, and docs are each covered by tasks.
- Placeholder scan: No `TBD` or open-ended "add tests" steps remain; each test step includes concrete examples.
- Type consistency: `CatTaskToolCommand`, `ReminderCommand`, `ContactTask`, `ProactiveState`, and existing `conversation_manager` APIs are used consistently.
