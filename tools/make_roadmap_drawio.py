#!/usr/bin/env python3
"""
Изработува .drawio датотека со две страници:
  1. Патоказ на проектот — фази, испораки и мерења
  2. Архитектура на системот — заштитениот процес од барање до докази

Датотеката се отвора во app.diagrams.net (draw.io) или во desktop верзијата.
Сите елементи се вистински облици, па се уредуваат и поместуваат како и секој
друг дијаграм. За Visio: во draw.io File → Export as → VSDX.

Извршување:  python3 tools/make_roadmap_drawio.py
"""
import html
from pathlib import Path

OUT = Path("/sessions/ecstatic-pensive-volta/mnt/thesis-net/docs/"
           "Патоказ-дипломска-Митровски-222022.drawio")

# ------------------------------------------------------------------ бои
NAVY = "#1F3864"
BLUE = "#2E5C8A"
LIGHT = "#DCE6F1"
GREEN = "#1E6B45"
LGREEN = "#DAEFE3"
AMBER = "#8A6116"
LAMBER = "#FBF0D9"
GREY = "#595959"
LGREY = "#F2F2F2"
RED = "#8B2C2C"
LRED = "#F8E3E3"


class Page:
    def __init__(self, name):
        self.name = name
        self.cells = []
        self.n = 1

    def _id(self):
        self.n += 1
        return f"n{self.n}"

    @staticmethod
    def _label(text):
        # Облиците се цртаат со html=1, па новиот ред мора да стигне до draw.io
        # како ознака <br>. Во XML атрибут таа мора да биде запишана избегнато
        # (&lt;br&gt;), инаку датотеката не е добро оформена.
        return html.escape(text).replace("\n", "&lt;br&gt;")

    def box(self, x, y, w, h, text, style):
        i = self._id()
        self.cells.append(
            f'<mxCell id="{i}" value="{self._label(text)}" style="{style}" '
            f'vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>'
            f'</mxCell>')
        return i

    def edge(self, src, dst, text="", style=""):
        i = self._id()
        base = ("edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeWidth=2;"
                f"strokeColor={GREY};endArrow=block;endFill=1;fontSize=11;"
                "fontFamily=Verdana;labelBackgroundColor=#FFFFFF;")
        self.cells.append(
            f'<mxCell id="{i}" value="{self._label(text)}" style="{base}{style}" '
            f'edge="1" parent="1" source="{src}" target="{dst}">'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>')
        return i

    def xml(self):
        body = "".join(self.cells)
        pid = "".join(ch for ch in self.name if ch.isalnum())[:12] or "page"
        return (f'<diagram id="{pid}" name="{html.escape(self.name)}">'
                f'<mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" '
                f'guides="1" tooltips="1" connect="1" arrows="1" fold="1" '
                f'page="1" pageScale="1" pageWidth="1654" pageHeight="1169" '
                f'math="0" shadow="0">'
                f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root>'
                f'</mxGraphModel></diagram>')


def st(fill, stroke, *, font=13, bold=1, align="center", rounded=1, dashed=0,
       fontColor="#000000", extra=""):
    return (f"rounded={rounded};whiteSpace=wrap;html=1;fillColor={fill};"
            f"strokeColor={stroke};strokeWidth=2;fontSize={font};"
            f"fontStyle={bold};fontFamily=Verdana;fontColor={fontColor};"
            f"align={align};verticalAlign=middle;arcSize=8;dashed={dashed};{extra}")


TEXT = ("text;html=1;strokeColor=none;fillColor=none;align=left;"
        "verticalAlign=top;fontSize=11;fontFamily=Verdana;whiteSpace=wrap;")


# =====================================================================
# Страница 1 — Патоказ
# =====================================================================
def page_roadmap():
    p = Page("Патоказ на проектот")

    p.box(60, 40, 1520, 50,
          "ПАТОКАЗ НА ДИПЛОМСКАТА РАБОТА — Примена на големи јазични модели "
          "за автоматизација и валидација на мрежни конфигурации",
          st(NAVY, NAVY, font=16, fontColor="#FFFFFF"))
    p.box(60, 96, 1520, 26,
          "Дамјан Митровски (222022) · ФИНКИ, Скопје · ментор: проф. д-р Александра Дединец",
          st("none", "none", font=11, bold=0, fontColor=GREY, extra="strokeWidth=0;"))

    fazi = [
        ("ФАЗА 1\nЛабораторија",
         "• Containerlab + FRRouting\n"
         "• 2 рутери, 3 сегменти\n"
         "• машински читлива намера\n  (intended_state.yaml)\n"
         "• 7 проверки: 3 достапност\n  + 4 конфигурациски"),
        ("ФАЗА 2\nДетерминистичка\nпроверка",
         "• строг договор за податоци\n  (Pydantic, без непознати полиња)\n"
         "• проверка на шема\n• проверка на топологија\n"
         "• проверка на политика\n• проверка на дозволен опсег"),
        ("ФАЗА 3\nЈазичен модел",
         "• локално извршување (Ollama)\n"
         "• излез ограничен со шема\n"
         "• три дозволени одлуки:\n  PROPOSE · CLARIFY · REFUSE\n"
         "• моделот нема пристап до\n  уредите"),
        ("ФАЗА 4\nОдобрување и\nпримена",
         "• одобрување од инженер\n  врзано со SHA-256\n"
         "• ограничен Ansible playbook\n"
         "• идемпотентна поправка\n"
         "• независна валидација по\n  примената"),
        ("ФАЗА 5\nМерење",
         "• 12 модели од 8 семејства\n"
         "• 848 извршувања во живо\n"
         "• 4 серии експерименти\n"
         "• зачувани докази со SHA-256\n"
         "• проширена топологија со\n  три истовремени грешки"),
        ("ФАЗА 6\nАнализа и\nпишување",
         "• матрица на конфузија\n"
         "• аблација со три услови\n"
         "• таксономија на грешките\n"
         "• веб-интерфејс за приказ\n"
         "• 46 страници, 8 поглавја,\n  10 слики, 12 табели, 18 извори"),
    ]

    x0, y0, w, gap = 60, 150, 235, 22
    prev_head = None
    for k, (naslov, sodrzina) in enumerate(fazi):
        x = x0 + k * (w + gap)
        head = p.box(x, y0, w, 74, naslov, st(BLUE, NAVY, font=13, fontColor="#FFFFFF"))
        p.box(x, y0 + 74, w, 176, sodrzina,
              st(LIGHT, NAVY, font=11, bold=0, align="left",
                 extra="verticalAlign=top;spacingLeft=10;spacingTop=8;"))
        if prev_head is not None:
            p.edge(prev_head, head)
        prev_head = head

    # --- четирите серии мерења ---
    y1 = y0 + 300
    p.box(60, y1 - 42, 1520, 32, "ЧЕТИРИ СЕРИИ МЕРЕЊА — вкупно 848 извршувања во живо",
          st(GREEN, GREEN, font=13, fontColor="#FFFFFF"))
    serii = [
        ("Заштитен процес", "360 извршувања", "барање → предлог низ\nцелата низа проверки"),
        ("Објаснување", "288 извршувања", "опис на постоечка\nконфигурација"),
        ("Контрафактичка\nаблација", "80 предлози", "одбиените предлози\nприменети во живо"),
        ("Слободна форма", "120 извршувања", "сурови команди\nбез никаква структура"),
    ]
    ws = 365
    for k, (ime, n, opis) in enumerate(serii):
        x = 60 + k * (ws + 20)
        p.box(x, y1, ws, 40, ime, st(LGREEN, GREEN, font=12))
        p.box(x, y1 + 40, ws, 30, n, st(LGREEN, GREEN, font=13, fontColor=GREEN))
        p.box(x, y1 + 70, ws, 52, opis,
              st("#FFFFFF", GREEN, font=10, bold=0))

    # --- резултати ---
    y2 = y1 + 160
    p.box(60, y2, 1520, 32, "ГЛАВНИ РЕЗУЛТАТИ",
          st(AMBER, AMBER, font=13, fontColor="#FFFFFF"))
    rez = [
        ("0% → 8% → 33%",
         "стапка на прекршување на политиката\nпри намалување на структурата"),
        ("1,000",
         "стапка на откривање на проверката\n(0 пропуштени од 156 извршувања)"),
        ("9 од 12",
         "модели дале барем еден предлог што ја\nкрши политиката — ниту еден не е применет"),
        ("1,00 наспроти 0,50–1,00",
         "безбедност на системот наспроти\nбезбедност на предлозите по модел"),
    ]
    for k, (glavno, opis) in enumerate(rez):
        x = 60 + k * (ws + 20)
        p.box(x, y2 + 32, ws, 44, glavno, st(LAMBER, AMBER, font=16, fontColor=AMBER))
        p.box(x, y2 + 76, ws, 48, opis, st("#FFFFFF", AMBER, font=10, bold=0))

    # --- преостанато ---
    y3 = y2 + 150
    p.box(60, y3, 1520, 30, "ПРЕОСТАНАТО",
          st(GREY, GREY, font=12, fontColor="#FFFFFF"))
    p.box(60, y3 + 30, 1520, 42,
          "освежување на содржината во Word (Ctrl+A, F9)   ·   "
          "испраќање на менторот за преглед   ·   "
          "внесување на забелешките   ·   подготовка на презентацијата за одбрана",
          st(LGREY, GREY, font=11, bold=0))
    return p


# =====================================================================
# Страница 2 — Архитектура
# =====================================================================
def page_arch():
    p = Page("Архитектура на системот")

    p.box(60, 30, 1300, 46,
          "ЗАШТИТЕН ПРОЦЕС — од барање на природен јазик до проверена мрежна состојба",
          st(NAVY, NAVY, font=15, fontColor="#FFFFFF"))

    # редот со одбивања се црта 120 px погоре, па главниот ред мора да остави
    # доволно простор под насловот
    y = 230
    barnje = p.box(60, y, 200, 70, "Барање на\nприроден јазик", st(LGREY, GREY))
    model = p.box(310, y, 220, 70,
                  "Локален јазичен модел\n(излез ограничен со шема)", st(LIGHT, BLUE))
    dogovor = p.box(580, y, 200, 70, "Договор за податоци\n(Pydantic)", st(LIGHT, BLUE))
    proverka = p.box(830, y, 240, 70,
                     "Детерминистичка проверка\nшема · топологија · политика · опсег",
                     st(LGREEN, GREEN, font=11))
    odobr = p.box(1120, y, 240, 70,
                  "Одобрување од инженер\nврзано со SHA-256", st(LGREEN, GREEN))

    y2 = y + 160
    ansible = p.box(1120, y2, 240, 70,
                    "Ansible\n(ограничен опсег на промени)", st(LGREEN, GREEN))
    validator = p.box(830, y2, 240, 70,
                      "Независна валидација\n7 проверки врз работната мрежа",
                      st(LGREEN, GREEN, font=11))
    dokazi = p.box(580, y2, 200, 70, "Зачувани докази\n(JSON + SHA-256)", st(LGREY, GREY))

    p.edge(barnje, model)
    p.edge(model, dogovor)
    p.edge(dogovor, proverka)
    p.edge(proverka, odobr, "поминува")
    p.edge(odobr, ansible, "APPROVE")
    p.edge(ansible, validator)
    p.edge(validator, dokazi)

    # одбивања
    odbien = p.box(830, y - 120, 240, 56,
                   "Предлогот е одбиен\nништо не се применува",
                   st(LRED, RED, font=11))
    p.edge(proverka, odbien, "не поминува", f"strokeColor={RED};dashed=1;")

    refuse = p.box(310, y - 120, 220, 56,
                   "REFUSE / CLARIFY\nмоделот не предлага промена",
                   st(LAMBER, AMBER, font=11))
    p.edge(model, refuse, "", f"strokeColor={AMBER};dashed=1;")

    namera = p.box(310, y2, 220, 70,
                   "Машински читлива намера\n(intended_state.yaml)", st(LAMBER, AMBER))
    p.edge(namera, proverka, "пред примена", f"strokeColor={AMBER};dashed=1;")
    p.edge(namera, validator, "по примена", f"strokeColor={AMBER};dashed=1;")

    p.box(60, y2 + 130, 1300, 92,
          "Клучно својство: моделот нема школка, нема пристап до наредбената линија на "
          "уредите, ниту до Docker или Ansible. Тој враќа само типизиран запис што "
          "понатаму го оценуваат детерминистички проверки и човек.\n\n"
          "Истата датотека со намера се користи и пред примената и по неа, што "
          "оневозможува проверката да се согласи сама со себе.",
          st("#FFFFFF", NAVY, font=12, bold=0, align="left",
             extra="spacingLeft=14;spacingTop=10;verticalAlign=top;"))
    return p


def main():
    pages = [page_roadmap(), page_arch()]
    xml = ('<mxfile host="app.diagrams.net" type="device">'
           + "".join(pg.xml() for pg in pages) + "</mxfile>")
    OUT.write_text(xml, encoding="utf-8")
    print(f"зачувано: {OUT.name}  ({len(xml) // 1024} KB, {len(pages)} страници)")


if __name__ == "__main__":
    main()
