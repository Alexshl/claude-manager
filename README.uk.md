# claude-manager (`cm`)

[English](README.md) | **Українська**

## Швидке встановлення

```bash
curl -fsSL https://raw.githubusercontent.com/Alexshl/claude-manager/main/quick-install.sh | bash
```

Інтерактивний інсталятор перевіряє залежності, ставить їх (з підтвердженням) і викликає `install.sh`.

Запускає й підтримує живими сесії Claude Code з Remote Control всередині tmux на домашній машині, щоб вони з'являлись у Claude Desktop на іншому комп'ютері. Мережевих серверів немає.

Канал керування — постійна «control»-сесія (`claude --remote-control "<host>-manager"`), що працює в `control/` цього репозиторію. Її `CLAUDE.md` вчить її користуватись `cm`.

## Вимоги

Python 3.10+ (лише stdlib), tmux, Claude Code (`claude` у PATH). macOS або Linux.

## Встановлення

```bash
./install.sh
claude   # один раз інтерактивно — залогінитись
```

Скрипт створює venv (`~/.local/share/claude-manager/venv`), лінкує `cm` у `~/.local/bin`, ставить launchd-агент (macOS) або systemd user unit (Linux, + `loginctl enable-linger`) із запуском `cm daemon`, і реєструє control-сесію. Повторний запуск безпечний.

Видалення: `./uninstall.sh` (`--purge` — ще й стан та логи).

## Команди

| Команда | Опис |
|---|---|
| `cm sessions [--dir X] [--limit N] [--json]` | збережені сесії Claude (`~/.claude/projects`) |
| `cm ls [--json]` | керовані сесії: ім'я, id, каталог, alive/dead, перезапуски |
| `cm start <dir> [--name N] [--mode M] [--create] [--clone URL]` | нова сесія; `--create` — `mkdir -p`; `--clone` — клонувати, якщо каталогу нема (`owner/repo` через `gh repo clone`, інакше `git clone`) |
| `cm resume <id\|префікс> [--name N] [--mode M] [--fork\|--force]` | взяти під керування збережену сесію (див. «Конфлікти») |
| `cm stop <id\|ім'я>` | зупинити й прибрати зі стану |
| `cm restart <id\|ім'я>` | перезапустити |
| `cm logs <id\|ім'я> [-n N]` | останній вивід терміналу |
| `cm daemon [--once] [--interval 30]` | цикл підтримки |
| `cm update` | `git pull --ff-only`, `pip install -e` якщо змінився pyproject, перезапуск сервісу демона (tmux-сесії живуть) |
| `cm control` | зареєструвати control-сесію |

Режими `--mode`: acceptEdits, auto, bypassPermissions, default, dontAsk, plan. За замовчуванням для всіх сесій (включно з control) — `auto`.

## Конфіг

`~/.config/claude-manager/config.json`:
- `default_permission_mode` — режим за замовчуванням (`auto`);
- `host_label` — назва хоста в імені сесії (інакше коротка назва хоста);
- `control_id` — фіксований id control-сесії (генерується сам).

Ім'я сесії в Claude Desktop: `<host>: <name>`, напр. `macmini: fdesk`; control — `macmini: manager`.

## Конфлікти при resume

Перед `cm resume` скануються процеси (`ps -axo pid,ppid,args`) на uuid сесії. Якщо її тримає чужий `claude` (поза нашим tmux) — відмова з поясненням. `--fork` запускає `claude --resume <id> --fork-session --session-id <новий>` і зберігає новий id. `--force` завершує чужий процес (SIGTERM, через 5 с SIGKILL), але лише якщо він без `remote-control`; remote-control процес не чіпається навіть з `--force`.

## Перезавантаження

Демон стартує разом із системою й піднімає всі бажані сесії через `--resume` (перший запуск не рахується в `restarts`). Сервіс налаштований так (`AbandonProcessGroup` / `KillMode=process`), що перезапуск демона (`cm update`) не вбиває tmux.

## Як це працює

- Бажаний стан: `~/.config/claude-manager/state.json` (з урахуванням `XDG_CONFIG_HOME`), запис атомарний, з файловим локом.
- tmux-сесія `cm`, по вікну на сесію (ім'я — перші 8 символів id).
- Нова сесія: `claude --session-id <uuid> --remote-control "<host>: <name>" --permission-mode auto`; якщо jsonl з таким id вже існує — `--resume <uuid>`.
- Демон перезапускає мертві сесії з експоненційним backoff (до 10 хв; скидається після 10 хв стабільної роботи). Лог: `~/.local/state/claude-manager/daemon.log`.
- Перед запуском каталог позначається довіреним у `~/.claude.json` (`projects[<path>].hasTrustDialogAccepted`); одноразова копія — `~/.claude.json.cm-bak`.

## Тести

```bash
python3 -m unittest discover -s tests -t .
```

## Безпека

Сесії працюють без нагляду в режимі `auto`. Змініть `default_permission_mode` у config.json, якщо це неприйнятно.
