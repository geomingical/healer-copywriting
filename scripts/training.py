"""Training record shared by the team: ♡ sentences, disliked sentences, banned words, final copies
(optionally with the user's edited version), and text-style notes (data/training.json).

prepare.py puts the latest of these into every task.md. "Training" only means this; no model is retrained.

Usage:
  python3 scripts/training.py heart "句子" [--route A] [--product ID]
  python3 scripts/training.py dislike "句子" [--route A] [--product ID]
  python3 scripts/training.py undislike "句子"
  python3 scripts/training.py ban 超級
  python3 scripts/training.py unban 超級
  python3 scripts/training.py final --product ID --name 商品名稱 --file 定稿.txt [--edited-file 修改後.txt]
  python3 scripts/training.py note add "短句規則" --source "來源說明" [--date YYYY-MM-DD]
  python3 scripts/training.py note list
  python3 scripts/training.py note remove N        # N 從 1 起算（note list 的編號）
  python3 scripts/training.py show
  (any command) --training path/to/training.json    # default data/training.json

data/training.json ships with the repo as Healer's base preferences; each clone keeps adding to it. A missing
record file reads as an empty record and is created by the first command that writes.
"""
import argparse
import datetime
import fcntl
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DEFAULT_PATH = HERE / 'data/training.json'
# Old files without 'disliked' or 'style_notes' read as empty lists; save() writes every key back.
KEYS = ('hearts', 'banned', 'finals', 'disliked', 'style_notes')
STYLE_NOTES_MAX = 30
NOTE_MAX_CHARS = 80   # both the rule and its source


def load(path: Path) -> dict:
    """The record at path; a missing file is an empty record."""
    path = Path(path)
    if not path.exists():
        return {k: [] for k in KEYS}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise SystemExit(f'{path}: expected a JSON object with {", ".join(KEYS)}')
    record = {k: data.get(k) or [] for k in KEYS}
    bad = [k for k in KEYS if not isinstance(record[k], list)]
    bad += [f'hearts[{i}]' for i, h in enumerate(record['hearts']) if not (isinstance(h, dict) and str(h.get('text') or '').strip())]
    bad += [f'banned[{i}]' for i, w in enumerate(record['banned']) if not (isinstance(w, str) and w.strip())]
    bad += [f'disliked[{i}]' for i, d in enumerate(record['disliked']) if not (isinstance(d, dict) and str(d.get('text') or '').strip())]
    bad += [f'finals[{i}]' for i, f in enumerate(record['finals']) if not (isinstance(f, dict) and str(f.get('text') or '').strip()
                                                                       and isinstance(f.get('edited_text', ''), str))]
    bad += [f'style_notes[{i}]' for i, n in enumerate(record['style_notes'])
            if not (isinstance(n, dict) and isinstance(n.get('note'), str) and n['note'].strip())]
    if bad:
        raise SystemExit(f'{path}: malformed entries: {", ".join(bad)}')
    return record


def record_path(value: str | None) -> Path:
    """Resolve a --training value. None (option not given) = the default record (data/training.json)."""
    if value is None:
        return Path(DEFAULT_PATH)
    return Path(value).expanduser().resolve()


def save(path: Path, record: dict) -> None:
    """Write atomically: a temp file in the same folder, then rename over the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False, indent=1) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


@contextmanager
def locked(path: Path):
    """Two people share one file: hold a lock around read-modify-write so neither overwrites the other."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + '.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def today() -> str:
    return datetime.date.today().isoformat()


# Edit helpers shared by this CLI and scripts/panel_session.py; each returns True when the record changed.
def add_sentence(record: dict, key: str, text: str, route: str | None = None, product_id: str | None = None) -> bool:
    """Append a ♡ ('hearts') or disliked ('disliked') sentence unless the same text is already there."""
    text = text.strip()
    if not text:
        raise SystemExit('empty sentence')
    if any(s['text'].strip() == text for s in record[key]):
        return False
    record[key].append({'text': text, 'route': route, 'product_id': product_id, 'added': today()})
    return True


def remove_sentence(record: dict, key: str, text: str) -> bool:
    text = text.strip()
    kept = [s for s in record[key] if s['text'].strip() != text]
    changed = len(kept) != len(record[key])
    record[key] = kept
    return changed


def add_banned(record: dict, word: str) -> bool:
    word = word.strip()
    if not word:
        raise SystemExit('empty word')
    if word in record['banned']:
        return False
    record['banned'].append(word)
    return True


def add_final(record: dict, product_id: str, name: str, text: str, replace: bool = False,
              edited_text: str | None = None) -> bool:
    """Append a final copy. The same product with the same text is never added twice; with replace=True an
    earlier final of the same product is overwritten (the panel lets people go back and edit a final).

    edited_text = the version the user actually used. None leaves it as it is; text sets or updates it on
    this product's final (never a new entry); an empty string removes it."""
    text = text.strip()
    if not text:
        raise SystemExit('empty final copy')
    same = [f for f in record['finals'] if f.get('product_id') == product_id]
    entry = next((f for f in same if f['text'].strip() == text), None)
    changed = False
    if entry is None:
        if replace and same:
            entry = same[-1]
            entry.update(name=name, text=text, saved=today())
        else:
            entry = {'product_id': product_id, 'name': name, 'text': text, 'saved': today()}
            record['finals'].append(entry)
        changed = True
    if edited_text is not None:
        changed |= set_edited_text(entry, edited_text)
    return changed


def set_edited_text(final: dict, edited_text: str) -> bool:
    """Set (or with '' remove) the user's edited version on one finals entry; True when it changed."""
    edited = edited_text.strip()
    if edited:
        if final.get('edited_text') == edited:
            return False
        final['edited_text'] = edited
        return True
    return final.pop('edited_text', None) is not None


def check_note_text(value: str, label: str) -> str:
    value = ' '.join(value.split())
    if len(value) > NOTE_MAX_CHARS:
        raise SystemExit(f'{label}太長：{len(value)} 字，上限 {NOTE_MAX_CHARS} 字。請改成更短的一句。')
    return value


def add_note(record: dict, note: str, source: str, added: str | None = None) -> bool:
    """Append a text-style note; an identical note is never added twice. Refuses past STYLE_NOTES_MAX."""
    note = check_note_text(note, '規則')
    if not note:
        raise SystemExit('規則是空的')
    source = check_note_text(source or '', '來源說明')
    if added is None:
        added = today()
    elif not re.fullmatch(r'\d{4}-\d{2}-\d{2}', added):
        raise SystemExit(f'日期格式要是 YYYY-MM-DD：{added}')
    else:
        try:
            datetime.date.fromisoformat(added)
        except ValueError:
            raise SystemExit(f'不是有效的日期：{added}') from None
    if any(n['note'].strip() == note for n in record['style_notes']):
        return False
    if len(record['style_notes']) >= STYLE_NOTES_MAX:
        raise SystemExit(f'文字偏好已經有 {len(record["style_notes"])} 條（上限 {STYLE_NOTES_MAX}）。'
                         '請先用 `training.py note list` 看編號，`note remove N` 刪掉過時或重複的再加。')
    record['style_notes'].append({'note': note, 'source': source, 'added': added})
    return True


def remove_note(record: dict, number: int) -> dict:
    """Remove note number (1-based, as in `note list`) and return it."""
    if not 1 <= number <= len(record['style_notes']):
        raise SystemExit(f'沒有第 {number} 條；目前共 {len(record["style_notes"])} 條（用 note list 看編號）')
    return record['style_notes'].pop(number - 1)


def note_lines(record: dict) -> list[str]:
    return [f'{k}. {n["note"]}' + (f'（來源：{n["source"]}）' if n.get('source') else '') +
            (f'  {n["added"]}' if n.get('added') else '') for k, n in enumerate(record['style_notes'], 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--training', default=None,
                        help='record file (default data/training.json)')
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--training', default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest='command', required=True)
    for command, label in (('heart', 'add a ♡ sentence'), ('dislike', 'add a disliked sentence (never write it again)')):
        sentence = sub.add_parser(command, parents=[common], help=label)
        sentence.add_argument('text')
        sentence.add_argument('--route', choices=['A', 'B', 'C'], type=str.upper)
        sentence.add_argument('--product', help='product id the sentence was written for')
    sub.add_parser('undislike', parents=[common], help='remove a disliked sentence').add_argument('text')
    sub.add_parser('ban', parents=[common], help='add a banned word').add_argument('word')
    sub.add_parser('unban', parents=[common], help='remove a banned word').add_argument('word')
    final = sub.add_parser('final', parents=[common], help='save a final copy')
    final.add_argument('--product', required=True)
    final.add_argument('--name', required=True, help='product name')
    final.add_argument('--file', required=True, help='text file with the final copy')
    final.add_argument('--edited-file', help="text file with the user's edited version (the one actually used)")
    note = sub.add_parser('note', parents=[common], help='text-style notes learned from the users\' edited copies')
    note_sub = note.add_subparsers(dest='note_command', required=True)
    note_add = note_sub.add_parser('add', parents=[common], help=f'add one rule (≤{NOTE_MAX_CHARS} 字)')
    note_add.add_argument('note')
    note_add.add_argument('--source', required=True, help=f'where it was learned (≤{NOTE_MAX_CHARS} 字), e.g. 專案 abcd1234 3 件商品')
    note_add.add_argument('--date', help='YYYY-MM-DD (default today)')
    note_sub.add_parser('list', parents=[common], help='list the rules with their numbers')
    note_sub.add_parser('remove', parents=[common], help='remove rule N (1-based)').add_argument('number', type=int)
    sub.add_parser('show', parents=[common], help='print the record')
    args = parser.parse_args()
    path = record_path(args.training)

    if args.command == 'show':
        record = load(path)
        edited = sum(1 for f in record['finals'] if f.get('edited_text'))
        print(f'{path}\n♡ 句子 {len(record["hearts"])} 句、不喜歡 {len(record["disliked"])} 句、'
              f'禁用詞 {len(record["banned"])} 個、定稿 {len(record["finals"])} 篇（含修改後版本 {edited} 篇）、'
              f'文字偏好 {len(record["style_notes"])} 條')
        for mark, key in (('♡', 'hearts'), ('✕', 'disliked')):
            for h in record[key]:
                print(f'{mark} {h["text"]}' + (f'（路線 {h["route"]}）' if h.get('route') else '') +
                      (f'  {h["product_id"]}' if h.get('product_id') else ''))
        if record['banned']:
            print('禁用：' + '、'.join(record['banned']))
        for f in record['finals']:
            print(f'定稿 {f.get("saved", "")} {f.get("product_id", "")} {f.get("name", "")}：{len(f["text"])} 字' +
                  (f'（修改後版本 {len(f["edited_text"])} 字）' if f.get('edited_text') else ''))
        if record['style_notes']:
            print('文字偏好：')
            print('\n'.join(note_lines(record)))
        return
    if args.command == 'note' and args.note_command == 'list':
        record = load(path)
        lines = note_lines(record)
        print('\n'.join(lines) if lines else '還沒有文字偏好。')
        print(f'共 {len(lines)} 條（上限 {STYLE_NOTES_MAX}）')
        return

    if args.command == 'final':
        text = Path(args.file).expanduser().read_text(encoding='utf-8').strip()
        if not text:
            raise SystemExit(f'{args.file} is empty')
        edited = Path(args.edited_file).expanduser().read_text(encoding='utf-8') if args.edited_file else None
    with locked(path):
        record = load(path)
        if args.command == 'note':
            if args.note_command == 'add':
                changed = add_note(record, args.note, args.source, args.date)
                note_text = check_note_text(args.note, '規則')
                message = (f'已加入文字偏好（第 {len(record["style_notes"])} 條）：{note_text}' if changed
                           else f'已經有這條文字偏好，沒有重複加入：{note_text}')
            else:
                removed = remove_note(record, args.number)
                changed = True
                message = f'已刪除第 {args.number} 條：{removed["note"]}'
        elif args.command in ('heart', 'dislike'):
            key, mark = ('hearts', ' ♡') if args.command == 'heart' else ('disliked', '不喜歡')
            text = args.text.strip()
            changed = add_sentence(record, key, text, args.route, args.product)
            message = f'已加入{mark}：{text}' if changed else f'已經有這句{mark}，沒有重複加入：{text}'
        elif args.command == 'undislike':
            text = args.text.strip()
            changed = remove_sentence(record, 'disliked', text)
            message = f'已取消不喜歡：{text}' if changed else f'不喜歡的句子裡沒有這句：{text}'
        elif args.command == 'ban':
            word = args.word.strip()
            changed = add_banned(record, word)
            message = f'已禁用「{word}」' if changed else f'「{word}」已經是禁用詞'
        elif args.command == 'unban':
            word = args.word.strip()
            changed = word in record['banned']
            if changed:
                record['banned'].remove(word)
            message = f'已取消禁用「{word}」' if changed else f'「{word}」不在禁用詞裡'
        else:
            changed = add_final(record, args.product, args.name, text, edited_text=edited)
            message = (f'已存定稿：{args.product} {args.name}（{len(text)} 字）' if changed
                       else f'這篇定稿已經存過，沒有重複加入：{args.product} {args.name}')
        if changed:
            save(path, record)
    print(message)


if __name__ == '__main__':
    sys.exit(main())
