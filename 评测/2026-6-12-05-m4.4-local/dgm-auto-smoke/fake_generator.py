from pathlib import Path
import sys
root = Path.cwd()
(root / '.mu' / 'prompts').mkdir(parents=True, exist_ok=True)
idx = 'one' if '1/2' in sys.argv[-1] else 'two'
(root / '.mu' / 'prompts' / f'auto-{idx}.md').write_text('auto prompt\n', encoding='utf-8')
print('fake generator done')
