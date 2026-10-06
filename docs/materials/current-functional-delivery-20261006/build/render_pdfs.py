"""Rebuild only the two documentary PDFs from authored pages, without database access."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

BASE = Path(__file__).resolve().parents[1]
GREEN = colors.HexColor('#13725A')
INK = colors.HexColor('#18352F')
MUTED = colors.HexColor('#566D66')
FONT = Path('C:/Windows/Fonts/msyh.ttc')
pdfmetrics.registerFont(TTFont('BFChinese', str(FONT), subfontIndex=0))
W,H = A4
BODY = ParagraphStyle('body',fontName='BFChinese',fontSize=13.1,leading=23,textColor=INK,
    alignment=TA_LEFT,wordWrap='CJK',spaceAfter=8)
CAPTION = ParagraphStyle('caption',parent=BODY,fontSize=8.7,leading=13,textColor=MUTED)
TABLE = ParagraphStyle('table',parent=BODY,fontSize=10.0,leading=15)


def para(c,text,x,y,width,style=BODY,min_y=76):
    p=Paragraph(escape(text).replace('\n','<br/>'),style)
    _,height=p.wrap(width,1200)
    if y-height < min_y:
        raise ValueError(f'Page content overflow at {text[:40]} ({y-height:.1f})')
    p.drawOn(c,x,y-height)
    return y-height


def render(name,kind,pages):
    target=BASE/'outputs'/name
    if target.exists():
        raise ValueError('Existing PDF retained; use new version filename')
    c=canvas.Canvas(str(target),pagesize=A4,pageCompression=1,invariant=1)
    c.setTitle('钱途有界 '+kind+' 当前功能版')
    c.setAuthor('BoundedFunds project source-bound materials')
    occupancy=[]
    for i,p in enumerate(pages,1):
        c.setFillColor(GREEN);c.rect(0,H-12,W,12,stroke=0,fill=1)
        c.setFont('BFChinese',9);c.drawString(46,H-40,'BOUNDED FUNDS  /  钱途有界  /  '+kind)
        c.setFillColor(INK)
        title_style=ParagraphStyle('title',parent=BODY,fontSize=24 if len(p['title'])<20 else 21,leading=31)
        y=para(c,p['title'],46,H-68,W-92,title_style)-8
        y=para(c,p['strap'],46,y,W-92,ParagraphStyle('strap',parent=BODY,fontSize=11,leading=17,textColor=GREEN))-15
        c.setStrokeColor(colors.HexColor('#C6DAD2'));c.line(46,y,W-46,y);y-=18
        for s in p['sections']:
            c.setFont('BFChinese',13);c.setFillColor(GREEN);c.drawString(46,y-13,s['heading']);y-=25
            y=para(c,s['text'],46,y,W-92)-12
        if p['table']:
            rows=[[Paragraph(escape(cell),TABLE) for cell in row] for row in p['table']]
            widths=[(W-92)/len(rows[0])]*len(rows[0])
            table=Table(rows,colWidths=widths,hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#EAF3EE')),
                ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
                ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),
                ('LINEBELOW',(0,0),(-1,-1),0.4,colors.HexColor('#C6DAD2'))]))
            _,h=table.wrap(W-92,1200)
            if y-h<105: raise ValueError(f'Table overflow page {i}')
            table.drawOn(c,46,y-h);y-=h+16
        occupancy.append({'page':i,'body_end_y':round(y,2),'title':p['title'],
            'text_characters':sum(len(s['text']) for s in p['sections'])})
        # Source IDs resolve to exact public bytes in the adjacent source index.
        para(c,'来源 '+', '.join(p['refs'])+' · 见 sources.json / evidence-index.md',46,69,W-92,CAPTION,min_y=40)
        c.setFillColor(MUTED);c.setFont('BFChinese',8)
        c.drawString(46,29,'当前功能材料 · 全部模拟 · WORKING / NOT ACCEPTANCE')
        c.drawRightString(W-46,29,f'{i:02d} / {len(pages):02d}')
        c.showPage()
    c.save()
    actual=len(PdfReader(target).pages)
    if actual!=len(pages):raise ValueError('Actual PDF page count differs')
    return {'path':target.relative_to(BASE).as_posix(),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'bytes':target.stat().st_size,'actual_pages':actual,'visual_review':'PENDING','layout':occupancy}


def main():
    data=json.loads((BASE/'pages.json').read_text('utf8'))
    result=[render('钱途有界_当前功能企划_12页_交付版.pdf','竞赛企划',data['proposal']),
        render('钱途有界_当前功能白皮书_30页_交付版.pdf','技术白皮书',data['whitepaper'])]
    report={'protocol':'bounded-funds-material-pdf-structural-delivery-v1','artifacts':result,
        'font':{'path':str(FONT),'sha256':hashlib.sha256(FONT.read_bytes()).hexdigest(),'subfont':0},
        'source_pages_sha256':hashlib.sha256((BASE/'pages.json').read_bytes()).hexdigest(),
        'product_acceptance':'INCOMPLETE','task_closed':False}
    (BASE/'build/pdf-structure-delivery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps([{k:v for k,v in x.items() if k!='layout'} for x in result],ensure_ascii=False))


if __name__=='__main__':main()
