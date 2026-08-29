#!/usr/bin/env python3
"""
Revise the thesis DOCX **in place**, preserving the author's own wording.

The document was rewritten by hand in Word, so it must not be regenerated from
a build script — that would discard the rewrite. This script therefore edits the
existing file: it fixes typographic defects, replaces a leftover placeholder,
and inserts new sections and figures by cloning the formatting of paragraphs
that are already in the document.

Run:  python3 tools/revise_thesis_docx.py
"""
import copy
import re
import sys
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt

REPO = Path("/sessions/ecstatic-pensive-volta/mnt/thesis-net")
SRC = REPO / "docs" / "Дипломска-работа-Митровски-222022.docx"
SHOTS = REPO / "docs" / "screenshots"


# --------------------------------------------------------------- utilities
def png_size(path: Path):
    """Width/height straight out of the PNG IHDR chunk."""
    b = path.read_bytes()
    return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")


def fit(path: Path, max_w_in=6.1, max_h_in=4.6):
    """Scale to the text width, but cap the height so the caption stays put."""
    w, h = png_size(path)
    width = max_w_in
    if width * h / w > max_h_in:
        width = max_h_in * w / h
    return Inches(round(width, 2))


class Doc:
    def __init__(self, path: Path):
        self.doc = Document(str(path))
        self.body = self.doc.element.body

    def paras(self):
        return self.doc.paragraphs

    def find(self, prefix, exact=False):
        """First paragraph whose text starts with (or equals) `prefix`."""
        for p in self.doc.paragraphs:
            t = re.sub(r"\s+", " ", p.text).strip()
            if (t == prefix) if exact else t.startswith(prefix):
                return p
        raise LookupError(f"paragraph not found: {prefix!r}")

    def clone_after(self, template, anchor, text):
        """Insert a copy of `template`'s formatting, carrying `text`, after `anchor`."""
        new = copy.deepcopy(template._p)
        anchor._p.addnext(new)
        from docx.text.paragraph import Paragraph
        para = Paragraph(new, anchor._parent)
        runs = para.runs
        if not runs:                       # no run to inherit from
            para.add_run(text)
        else:
            runs[0].text = text
            for extra in runs[1:]:
                extra._element.getparent().remove(extra._element)
        return para

    def picture_after(self, anchor, image: Path, width):
        """Append a picture paragraph then move it into place after `anchor`."""
        para = self.doc.add_paragraph()
        para.alignment = 1                                  # centred
        para.add_run().add_picture(str(image), width=width)
        anchor._p.addnext(para._p)
        return para


def replace_text(doc: Document, old: str, new: str) -> int:
    """Replace `old` with `new`, keeping every run's own formatting.

    Collapsing a paragraph into a single run is destructive: a paragraph may
    carry a monospace run for an identifier, an italic run for a term, and so
    on. So the replacement is done inside the one run that contains the text,
    and a replacement spanning several runs is refused rather than flattened.
    """
    hits = 0
    for p in doc.paragraphs:
        if old not in p.text:
            continue
        for r in p.runs:
            if old in r.text:
                r.text = r.text.replace(old, new)
                hits += 1
                break
        else:
            raise RuntimeError(
                f"{old!r} spans multiple runs; replacing it would drop inline "
                f"formatting. Edit this paragraph explicitly instead."
            )
    return hits


# --------------------------------------------------------------------- main
def main():
    d = Doc(SRC)
    doc = d.doc
    log = []

    # ---- 1. typographic defects (all three are dropped leading characters) --
    fixes = [
        ("утерите се меѓусебно поврзани", "Рутерите се меѓусебно поврзани"),
        ("ако референтна вредност се користи content_violates_intent",
         "Како референтна вредност се користи content_violates_intent"),
        ("[РЕФЕРЕНЦА]", "[1, 2]"),
    ]
    for old, new in fixes:
        n = replace_text(doc, old, new)
        log.append(f"{'fixed ' if n else 'MISSED'} {n}x  {old[:46]!r}")

    # the pipeline line begins mid-word; it is a sentence, so it takes a capital
    for p in doc.paragraphs:
        if p.text.startswith("барање → локален јазичен модел"):
            p.runs[0].text = p.runs[0].text.replace("барање →", "Барање →", 1)
            log.append("fixed  1x  'барање →' -> 'Барање →'")
            break

    # ---- formatting templates lifted from the document itself --------------
    t_h2 = d.find("5.9 Имплементирани безбедносни својства")
    t_h3 = d.find("5.6.1 Проширување на поголема топологија")
    t_cap = d.find("Слика 6.1.")
    t_body = d.find("Овие својства важат само за")

    # ---- 2. renumber the existing second figure (a new 6.2 precedes it) ----
    n = replace_text(doc, "Слика 6.2. Recall", "Слика 6.3. Recall")
    log.append(f"{'renumbered' if n else 'MISSED'} Слика 6.2 -> 6.3")

    # ---- 3. new section 5.10, with the two dashboard figures ---------------
    anchor = doc.paragraphs[[i for i, p in enumerate(doc.paragraphs)
                             if re.sub(r"\s+", " ", p.text).strip().startswith("Овие својства важат само за")][0]]

    h = d.clone_after(t_h2, anchor, "5.10 Интерактивна демонстрација на процесот")
    p1 = d.clone_after(t_body, h,
        "Компонентите опишани во претходните делови се извршуваат преку наредбена линија и "
        "како резултат создаваат структурирани JSON записи. За потребите на демонстрација и "
        "за квалитативно набљудување на однесувањето на системот, изработен е и веб-интерфејс "
        "во кој целиот процес е прикажан на еден екран. Интерфејсот не воведува нова "
        "функционалност: тој ги повикува истите модули за предлагање, проверка, одобрување, "
        "примена и валидација, поради што не претставува втор, послабо контролиран пат до "
        "мрежните уреди.")
    p2 = d.clone_after(t_body, p1,
        "Топологијата се чита директно од декларативната датотека на Containerlab, така што "
        "структурата на мрежата се реконструира без рачно внесени податоци. Јазлите се "
        "распознаваат според својата вистинска улога: контејнер што извршува FRRouting се "
        "препознава како рутер, контејнер конфигуриран како Linux мост се препознава како "
        "комутатор, а преостанатите јазли се третираат како крајни уреди. Ова распознавање е "
        "потребно бидејќи Containerlab нема посебен тип за комутатор, па без него комутаторите "
        "би биле прикажани како обични хостови. Резултатот е прикажан на Слика 5.1.")
    img1 = d.picture_after(p2, SHOTS / "56_2026-08-12_19-18.png",
                           fit(SHOTS / "56_2026-08-12_19-18.png", 6.1, 3.6))
    c1 = d.clone_after(t_cap, img1,
        "Слика 5.1. Веб-интерфејс со приказ на процесот. Левиот дел ја прикажува автоматски "
        "реконструираната топологија со три рутери, три комутатори и пет крајни уреди, а "
        "десниот дел ги прикажува резултатите од независната валидација заедно со состојбата "
        "на рутирачките табели и филтерските правила прочитани од самите уреди.")
    p3 = d.clone_after(t_body, c1,
        "Во горниот дел на интерфејсот е поставен избирач на модел. Со промена на моделот "
        "целата анализа се извршува повторно врз истите влезни податоци, што овозможува "
        "непосредна споредба на две различни толкувања на иста мрежа. Ознаката во заглавието "
        "го прикажува резултатот од независната валидација, а не оцена на моделот: таа добива "
        "вредност MATCHES_INTENT само кога сите проверки изведени од дефинираната намера се "
        "успешни.")
    p4 = d.clone_after(t_body, p3,
        "Панелот со оцена на топологијата ја илустрира поделбата на одговорности што е "
        "централна за овој труд. Бројните показатели, предностите и ограничувањата, како и "
        "рангираните препораки прикажани на Слика 5.2, се пресметуваат детерминистички од "
        "топологијата и од дефинираната намера и не се генерирани од моделот. Улогата на "
        "моделот е ограничена на краткиот описен текст над нив. На тој начин, содржината што "
        "корисникот би можел да ја разбере како препорака за дејство останува проверлива, "
        "додека јазичниот модел придонесува само во делот каде евентуална грешка нема "
        "оперативни последици.")
    img2 = d.picture_after(p4, SHOTS / "57_2026-08-12_19-18.png",
                           fit(SHOTS / "57_2026-08-12_19-18.png", 6.1, 4.4))
    c2 = d.clone_after(t_cap, img2,
        "Слика 5.2. Детерминистички пресметана оцена на топологијата. Предностите, "
        "ограничувањата и рангираните препораки се изведени од декларираната топологија и "
        "намера; текстот над нив е единствениот дел произведен од јазичниот модел.")
    d.clone_after(t_body, c2,
        "Секое барање испратено преку интерфејсот поминува низ истите проверки како и барањата "
        "испратени од наредбена линија. Кога предлогот ќе биде одбиен, интерфејсот ја прикажува "
        "причината за одбивањето заедно со полето што го предизвикало, со што однесувањето на "
        "проверката станува видливо наместо непрозирно. Кога предлогот ќе биде одобрен и "
        "применет, состојбата на уредите се запишува пред и по примената и се прикажува "
        "разликата меѓу нив, така што ефектот од промената е документиран, а не претпоставен.")
    log.append("inserted section 5.10 with Слика 5.1 and Слика 5.2")

    # ---- 4. new Слика 6.2 in the three-condition section -------------------
    anchor = doc.paragraphs[[i for i, p in enumerate(doc.paragraphs)
                             if re.sub(r"\s+", " ", p.text).strip().startswith("Девет од дванаесетте модели")][0]]
    q1 = d.clone_after(t_body, anchor,
        "Условот со слободна форма бара посебна забелешка за начинот на спроведување. "
        "Командите произведени од моделот се извршуваат непосредно врз работните уреди, поради "
        "што по секое извршување лабораторијата мора да се врати во почетна состојба. Во "
        "спротивно, штетата од едно извршување би се пренела врз следното и мерењето би било "
        "неважечко. Конзолниот запис на Слика 6.2 ја прикажува токму таа постапка: секој ред "
        "претставува едно живо извршување, а ознаките за прекршена политика и за прекината "
        "поврзаност се доделуваат од независната валидација, а не од толкување на текстот "
        "вратен од моделот.")
    img3 = d.picture_after(q1, SHOTS / "23_2026-08-01_08-31.png",
                           fit(SHOTS / "23_2026-08-01_08-31.png", 5.4, 4.7))
    c3 = d.clone_after(t_cap, img3,
        "Слика 6.2. Конзолен запис од условот со слободна форма. Секој ред е едно живо "
        "извршување, а резултатот MATCHES_INTENT или DOES_NOT_MATCH_INTENT потекнува од "
        "независната валидација. Долниот блок ги сумира сите 120 извршувања во овој услов.")
    q2 = d.clone_after(t_body, c3,
        "Од вкупно 120 извршувања во овој услов, 53 ја оставија мрежата во состојба што не "
        "одговара на дефинираната намера, 40 ја прекршија безбедносната политика, 29 го "
        "отстранија правилото за забрана, а 14 прекинаа задолжителна поврзаност. Од 199 "
        "извршени команди, 55 беа одбиени од самиот уред како синтаксички или семантички "
        "неважечки, што покажува дека дел од заштитата во овој услов произлегува случајно — од "
        "строгоста на самиот рутер, а не од намерно воспоставена контрола.")
    d.clone_after(t_body, q2,
        "Најсериозниот наод во оваа кампања не е бројот на прекршувања, туку тоа што четири "
        "извршувања ја оставија лабораториската околина во состојба што не можеше да се поправи "
        "со ниту една реконфигурациска команда, поради што беше потребно целосно повторно "
        "поставување на топологијата. Во продукциска мрежа тоа одговара на состојба во која "
        "уредот повеќе не е достапен по мрежен пат и бара интервенција надвор од редовниот "
        "канал за управување.")
    log.append("inserted Слика 6.2 and surrounding analysis in section 6.3")

    # ---- 5. new subsection 6.6.1 with the live-network figure --------------
    anchor = doc.paragraphs[[i for i, p in enumerate(doc.paragraphs)
                             if re.sub(r"\s+", " ", p.text).strip().startswith("Дополнителните тестови со намерно погрешни")][0]]
    h3 = d.clone_after(t_h3, anchor,
        "6.6.1 Опис на жива мрежа и влијание на формулацијата на барањето")
    r1 = d.clone_after(t_body, h3,
        "Кампањата опишана погоре ги чита конфигурациските датотеки како текст. За да се "
        "провери дали истиот режим на откажување се јавува и кога описот се изведува од "
        "работна мрежа, спроведена е дополнителна кампања во која состојбата се чита во живо "
        "од рутерите — рутирачки табели и филтерски правила прочитани непосредно од уредите — "
        "по што моделот составува краток опис наменет за инженер кој мрежата ја гледа за "
        "првпат.")
    r2 = d.clone_after(t_body, r1,
        "Оваа кампања откри методолошки проблем што заслужува да биде наведен експлицитно, "
        "бидејќи лесно може да остане незабележан. Во првата верзија, барањето упатено до "
        "моделот содржеше формулација со која се бараше да се опише „какво ограничување на "
        "пристапот е поставено“. Потоа се мереше токму застапеноста на тоа ограничување во "
        "одговорот. Под таква формулација сите дванаесет модели постигнаа целосен резултат, "
        "што не претставува мерење на однесувањето на моделот, туку на самото барање: "
        "прашањето веќе го содржеше очекуваниот одговор.")
    r3 = d.clone_after(t_body, r2,
        "Барањето беше преформулирано во неутрална форма што не споменува политика, "
        "ограничувања ниту правила, туку бара единствено опис на мрежата. Под неутрална "
        "формулација резултатот е поинаков и позначаен: еден модел не го спомна пристапното "
        "ограничување во ниту едно од трите повторувања, а кај повеќе модели насоката на "
        "ограничувањето остана неопределена, како што е прикажано на Слика 6.4.")
    img4 = d.picture_after(r3, SHOTS / "45_2026-08-12_18-41.png",
                           fit(SHOTS / "45_2026-08-12_18-41.png", 5.6, 4.8))
    c4 = d.clone_after(t_cap, img4,
        "Слика 6.4. Опис на жива топологија при неутрално формулирано барање. Колоната deny "
        "покажува дали пристапното ограничување воопшто е спомнато, колоната dir дали неговата "
        "насока е точно определена, а halluc го дава бројот на измислени мрежни елементи.")
    d.clone_after(t_body, c4,
        "Заклучокот е дека изоставувањето на политиката не е стабилно својство на моделот, туку "
        "зависи од начинот на кој е поставено барањето. Модел што дава исправен опис кога е "
        "прашан непосредно за безбедноста може да го изостави истиот податок кога е прашан "
        "општо. Тоа ја зајакнува, а не ја ослабува, потребата од детерминистичка проверка во "
        "однос на дефинираната намера: сигурноста не смее да зависи од тоа дали корисникот се "
        "сетил да го постави вистинското прашање.")
    log.append("inserted subsection 6.6.1 with Слика 6.4")

    doc.save(str(SRC))
    print("\n".join(log))
    print(f"\nsaved: {SRC.name}")


if __name__ == "__main__":
    sys.exit(main())
