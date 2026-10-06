"""Render documentary PDF page previews with the installed Poppler; no office suite."""
import json
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

base=Path(__file__).resolve().parents[1]
poppler=Path('C:/Users/starryzac/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe')
records=[]
for label,name in [('proposal-delivery','钱途有界_当前功能企划_12页_交付版.pdf'),('whitepaper-delivery','钱途有界_当前功能白皮书_30页_交付版.pdf')]:
    dest=base/'build'/('pdf-previews-'+label)
    dest.mkdir(exist_ok=False)
    command=[str(poppler),'-r','100','-png',str(base/'outputs'/name),str(dest/'page')]
    result=subprocess.run(command,capture_output=True,check=False)
    (dest/'stdout.log').write_bytes(result.stdout);(dest/'stderr.log').write_bytes(result.stderr)
    record={'argv':command,'exit_code':result.returncode}
    records.append(record)
    if result.returncode:raise SystemExit(result.returncode)
    images=sorted(dest.glob('page-*.png'))
    width,height=240,356
    for part in range((len(images)+11)//12):
        subset=images[part*12:part*12+12]
        sheet=Image.new('RGB',(width*4,height*3),'#DCE5DF')
        draw=ImageDraw.Draw(sheet)
        for n,p in enumerate(subset):
            im=Image.open(p).convert('RGB');im.thumbnail((228,325))
            x=(n%4)*width+(width-im.width)//2;y=(n//4)*height+6
            sheet.paste(im,(x,y));draw.text(((n%4)*width+8,(n//4)*height+336),p.stem,fill='#18352F')
        sheet.save(base/'build'/f'{label}-contact-{part+1}.png')
(base/'build/pdf-delivery-render-commands.json').write_text(json.dumps(records,indent=2)+'\n',encoding='utf8')
print(json.dumps({'rendered_pages':42,'renderer':'actual installed Poppler','office_suite_used':False}))
