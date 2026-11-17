"""Red-flag phrases in a caller transcript (contracts §6.2, third trigger).

This matcher is a demo default. **It was written without clinical review.** Before
any real clinic uses the agent, a licensed dentist must validate the red-flag list
(these phrase lists, the triage fields and the `emergency` text), and the validation
must be recorded in the repository with the reviewer's role and the date of the
review (contracts §6.2, decision D13).

What it does: it reads one final caller transcript at a time, echo transcripts
included, and reports the red flags it recognises. The caller's language does not
matter: both lists are always checked, because callers switch mid-sentence. It errs
towards firing, because a false alarm costs one conditional recorded sentence that
does not claim the caller is in danger, while a missed red flag costs much more.

The phrase lists below are regular expressions over *normalised* text: lowercase,
accent-free, every character that is not a letter or a digit turned into a space.
So "can't" reads as "can t" and "¿Dificultad?" as "dificultad"; a pattern like
`can ?t breathe` covers both "cant breathe" and "can t breathe".

Each signal has `fires` patterns and `cancels` patterns. A `cancels` match takes the
signal away for that transcript: a plain negation right before the word ("no tengo
fiebre", "no fever"), or an unrelated sense of the same word ("de golpe", "it hit
me that..."). Playback limits and when to run the matcher are the call plumbing's
job, not this module's.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Reasons, in the priority order of contracts §6.2.
REASONS = (
    "red_flag_breathing",
    "red_flag_swelling_fever",
    "red_flag_trauma",
    "red_flag_bleeding",
)


@dataclass(frozen=True)
class Phrases:
    """Patterns of one signal in one language, over normalised text."""

    fires: tuple[str, ...]
    cancels: tuple[str, ...] = ()


# --- trouble breathing -----------------------------------------------------

BREATHING_ES = Phrases(
    fires=(
        r"no (puedo|puede|podia|consigo|consigue|logro) respirar",
        r"(me|le|nos) cuesta (mucho |muchisimo |un poco )?respirar",
        r"cuesta respirar",
        r"dificultad(es)? (para|al|en) respirar",
        r"apenas (puedo|puede) respirar",
        r"respir\w* (muy |bastante )?mal",
        r"no respira bien",
        r"respira con dificultad",
        r"(me|le) falta (el )?aire",
        r"falta de aire",
        r"no (me|le) llega (el )?aire",
        r"me quedo sin aire",
        r"(me ahogo|se ahoga|me estoy ahogando|se esta ahogando|ahogandose|ahogandome)",
        r"(me|se) asfixi",
        r"(se me|se le) cierra la garganta",
        r"tengo la garganta cerrada",
        r"(me|le) cuesta tragar",
        r"no (puedo|puede) (ni )?tragar",
        r"no (puedo|puede) (ni )?hablar bien de",
        r"pit[oa] al respirar",
        r"silbido al respirar",
    ),
    cancels=(
        r"no (tengo|tiene|hay) (nada de )?(dificultad|problema|problemas) (para|al) respirar",
        r"sin dificultad (para|al) respirar",
        r"(respiro|respira) (bien|perfectamente|normal)",
        r"nada de (falta de aire|dificultad)",
    ),
)

BREATHING_EN = Phrases(
    fires=(
        r"can ?t breathe",
        r"cannot breathe",
        r"could ?n ?t breathe",
        r"(trouble|difficulty|difficulties|problems?) breathing",
        r"(hard|difficult|struggling|struggle) to breathe",
        r"(short of|shortness of|out of) breath",
        r"can ?t catch (my|his|her|their) breath",
        r"fighting for breath",
        r"gasping",
        r"(choking|chocking|suffocating|wheezing)",
        r"(my|his|her) throat is closing",
        r"throat (is )?closing up",
        r"can ?t swallow",
        r"(struggling|struggle) to swallow",
        r"(my|his|her) airway",
        r"breathing (is|was) (really |very |quite )?(bad|difficult|hard|rough|laboured)",
        r"can (hardly|barely) breathe",
        r"breathing funny",
    ),
    cancels=(
        r"no (trouble|difficulty|problem|problems) breathing",
        r"not (struggling|struggle) to breathe",
        r"breathing (is )?(fine|ok|okay|normal|normally)",
        r"can breathe (fine|ok|okay|normally)",
        r"(bad|smelly) breath",
        r"breath (smells|smell)",
    ),
)

# --- swelling (one half of swelling with fever) -----------------------------

SWELLING_ES = Phrases(
    fires=(
        r"hinch(ad|azon|and|a |o )",
        r"inflamad[oa]",
        r"inflamacion",
        r"(flemon|absceso|abceso|abseso)",
        r"(un|una) (bola|bolita|bulto|pelota)",
        r"como un globo",
        r"(la cara|la mejilla|el carrillo|media cara) (muy |bastante )?(gord|grande|deformad)",
        r"el doble de (grande|tamano)",
        r"se (me|le) ha puesto la cara",
    ),
    cancels=(
        r"no (tengo|tiene|hay) (nada de )?(hinchazon|inflamacion|flemon)",
        r"sin (hinchazon|inflamacion|flemon)",
        r"no (esta|lo tengo|la tengo|lo tiene|la tiene) hinchad[oa]",
        r"no (se me|se le) (ha )?hinch",
        r"nada hinchad[oa]",
    ),
)

SWELLING_EN = Phrases(
    fires=(
        r"swo?llen",
        r"swelling",
        r"swells?",
        r"swelled",
        r"puffy",
        r"puffed up",
        r"(abscess|absess|abcess)",
        r"inflamed",
        r"inflammation",
        r"(a|big|hard) lump",
        r"ballooned",
        r"size of a (golf ball|tennis ball|egg)",
        r"(twice|double) the size",
    ),
    cancels=(
        r"no swelling",
        r"not swollen",
        r"is ?n ?t swollen",
        r"nothing (is )?swollen",
        r"no sign of swelling",
        r"no lump",
    ),
)

# --- fever (the other half) -------------------------------------------------

FEVER_ES = Phrases(
    fires=(
        r"fiebre",
        r"febril",
        r"decimas",
        r"destemplad[oa]",
        r"tiritando",
        r"escalofrios",
        r"(tengo|tiene) (mucha |algo de )?temperatura",
        r"temperatura alta",
        r"(3[789]|4[01]) (de |y )?(fiebre|temperatura|grados)",
        r"\b3[789] [0-9]\b",
        r"treinta y (siete|ocho|nueve)",
        r"cuarenta (de fiebre|grados)",
        r"(me|le) arde la cabeza",
    ),
    cancels=(
        r"no (tengo|tiene|hay|he tenido|ha tenido) (nada de )?(fiebre|temperatura|decimas)",
        r"sin (fiebre|temperatura|decimas)",
        r"nada de fiebre",
        r"fiebre no (tengo|tiene|he tenido)",
        r"no (me|le) ha dado fiebre",
        r"(la )?temperatura (esta|la tengo) (bien|normal)",
    ),
)

FEVER_EN = Phrases(
    fires=(
        r"fever",
        r"feverish",
        r"temperature",
        r"high temp",
        r"temp of",
        r"running a temp",
        r"shivering",
        r"chills",
        r"hot and cold",
        r"burning up",
        r"thirty (seven|eight|nine)",
        r"forty degrees",
        r"(3[789]|4[01]) (degrees|point)",
        r"(hundred|a hundred) and (one|two|three|four)",
    ),
    cancels=(
        r"no (fever|temperature|high temperature)",
        r"not? got (a |any )?(fever|temperature)",
        r"have ?n ?t got (a |any )?(fever|temperature)",
        r"do ?n ?t have (a |any )?(fever|temperature)",
        r"does ?n ?t have (a |any )?(fever|temperature)",
        r"(temperature|temp) (is|was) (normal|fine|ok|okay)",
        r"without a (fever|temperature)",
        r"not feverish",
        r"no sign of (a )?(fever|temperature)",
    ),
)

# --- a blow or an accident to the face or mouth ------------------------------
# A blow on its own is not enough: it needs a link to the face, the mouth, the
# teeth or the jaw (FACE_*), or a clear injury (INJURY_*). The direct patterns
# already carry that link. The cancels hold the senses that are not an injury now:
# a blow long ago, "de golpe" (suddenly), "it hit me that", a filling that fell out,
# something done "by accident".

BLOW_ES = Phrases(
    fires=(
        r"(?<!de )golpe",
        r"golpead[oa]",
        r"(me|se|te) golpe",
        r"(me|se|te|nos) (he |ha |has |hemos )?ca(i|y)(do|o|)",
        r"(me|se|te) (he|ha|has) dado (un|una) (golpe|tortazo|porrazo|trompazo)",
        r"(me|se|te) (he|ha|has) dado (de morros|con la|con el)",
        r"(le|me|nos) (han|ha) dado (un|una) (golpe|pelotazo|balonazo|codazo|punetazo|patada)",
        r"(le|me|nos) (han|ha) dado con (un|una|el|la)",
        r"caida",
        r"(?<!por )accidente",
        r"atropell",
        r"(choque|chocado|choco con|colision)",
        r"(patada|codazo|punetazo|pelotazo|balonazo|trompazo|porrazo|rodillazo)",
        r"(me|se) (he|ha) estampado",
        r"de morros",
        r"(me|se) (he|ha) (estrellado|empotrado)",
        r"traumatismo",
        r"tortazo",
    ),
    cancels=(
        r"de golpe",
        r"golpe de (calor|suerte|estado)",
        r"por accidente",
        r"se (me|le|te) (ha )?(caido|cayo) (un|una|el|la)"
        r" (empaste|corona|funda|puente|provisional|diente de leche)",
        r"hace \w+ (anos|ano|meses|mes|semanas|semana)",
        r"el (ano|mes) pasado",
        r"de pequen[oa]",
        r"cuando era (nin[oa]|peque|pequen[oa]|joven|chaval)",
        r"hace (mucho|tiempo|bastante)",
    ),
)

BLOW_EN = Phrases(
    fires=(
        r"(got|was|were|been|just) hit",
        r"hit (me|him|her|them|my|his|their) (in|on|with|right)",
        r"hit (in|on) the",
        r"hit (my|his|her) (face|mouth|tooth|teeth|jaw|lip|chin|nose|head)",
        r"(ball|door|elbow|fist|bat|stick|knee|car|puck|kerb|curb) hit",
        r"hit the (floor|ground|pavement|kerb|curb|wall|table|edge|step)",
        r"knocked",
        r"(i|he|she|we|they) fell",
        r"(fell|fallen|falling) (off|over|down|on|in|into|onto|out of)",
        r"had a fall",
        r"took a tumble",
        r"came off (my|his|her|the) (bike|scooter|skateboard|horse)",
        r"tripped (over|up|on|and)",
        r"\baccident\b",
        r"(car crash|crashed|collided)",
        r"smashed (my|his|her|into|his|her)",
        r"(punched|elbowed|headbutt|kicked in|whacked|clipped)",
        r"banged (my|his|her|it)",
        r"struck (in|on) the",
        r"(rugby )?tackled",
        r"over the handlebars",
        r"face (planted|first)",
        r"(blow|knock) to the",
    ),
    cancels=(
        r"it hit me",
        r"hit me (that|how)",
        r"hit a nerve",
        r"by accident",
        r"accidentally",
        r"(years?|months?|weeks?) ago",
        r"last (year|month)",
        r"when i was (a )?(kid|child|young|little|boy|girl)",
        r"as a (kid|child)",
        r"a long time ago",
        r"(filling|crown|cap|bridge|veneer) (fell|came) (out|off)",
    ),
)

# Direct patterns: a blow that already names the face, the mouth or the accident.
TRAUMA_DIRECT_ES = Phrases(
    fires=(
        r"golpe en (la|el|los|las)",
        r"accidente de (coche|moto|bici|bicicleta|trafico|patinete)",
        r"de morros",
        r"(me|se) (he|ha) caido de (la|el|las|los)",
        r"caida de (la|el) (bici|bicicleta|moto|escalera|silla|cama|columpio|patinete)",
        r"(le|me) (ha|han) dado (un|una) (pelotazo|balonazo|codazo|punetazo) en",
    ),
    cancels=BLOW_ES.cancels,
)

TRAUMA_DIRECT_EN = Phrases(
    fires=(
        r"hit (in|on) the (face|mouth|jaw|teeth|tooth|lip|chin|nose|head)",
        r"blow to the (face|mouth|jaw|head)",
        r"knocked (my|his|her|a|the) (tooth|teeth|front tooth) out",
        r"car (accident|crash)",
        r"(fell|came) off (my|his|her|the) (bike|scooter|skateboard|ladder|horse)",
        r"fell (down|off) the (stairs|steps|ladder)",
        r"punched in the (face|mouth|jaw|nose)",
        r"face (planted|first) (on|into|in)",
    ),
    cancels=BLOW_EN.cancels,
)

FACE_ES = Phrases(
    fires=(
        r"(cara|boca|mejilla|carrillo|pomulo|barbilla|menton|nariz|labio)",
        r"(diente|dientes|muela|muelas|dentadura|incisivo|colmillo|paleta|paletas)",
        r"(mandibula|encia|encias|paladar|lengua|maxilar)",
        r"(los|las) dos de delante",
    ),
)

FACE_EN = Phrases(
    fires=(
        r"(face|mouth|cheek|chin|jaw|nose|lip|lips)",
        r"(tooth|teeth|molar|incisor|denture|dentures|braces)",
        r"(gum|gums|palate|tongue)",
    ),
)

INJURY_ES = Phrases(
    fires=(
        r"sangr",
        r"herida",
        r"(roto|rota|partido|partida|astillado|rajado|quebrado)",
        r"se (me|le) (mueve|ha movido)",
        r"(suelto|suelta|flojo|floja|bailando)",
        r"(me|le) duele",
        r"dolor",
        r"hinch(ad|azon)",
        r"(me|le) falta (un|medio) (diente|trozo)",
    ),
)

INJURY_EN = Phrases(
    fires=(
        r"(blood|bleed|bleeding)",
        r"\bcut\b",
        r"(broken|broke|chipped|cracked|snapped)",
        r"(loose|wobbly|moving)",
        r"(hurts|hurt|pain|painful|sore)",
        r"swo?llen",
        r"missing (a|half a) (tooth|chunk)",
    ),
)

# --- bleeding that will not stop --------------------------------------------
# Bleeding alone is not a red flag: it needs a sign that it does not stop or that
# there is a lot of it.

BLEEDING_ES = Phrases(
    fires=(
        r"sangr",
        r"hemorragia",
    ),
    cancels=(
        r"no (me |le |te )?sangra",
        r"no (esta|estaba) sangrando",
        r"(ha|he) (parado|dejado) de sangrar",
        r"se (ha|le ha) cortado la sangre",
        r"no hay sangre",
        r"sin sangre",
        r"no sangro",
        r"ya no (me |le )?sale sangre",
    ),
)

BLEEDING_EN = Phrases(
    fires=(
        r"bleed",
        r"blood",
        r"(haemorrhag|hemorrhag)",
    ),
    cancels=(
        r"not bleeding",
        r"is ?n ?t bleeding",
        r"was ?n ?t bleeding",
        r"no (blood|bleeding)",
        r"stopped bleeding",
        r"bleeding (has )?stopped",
        r"does ?n ?t bleed",
        r"did ?n ?t bleed",
    ),
)

HEAVY_ES = Phrases(
    fires=(
        r"no (se |me |le )*(para|corta)",
        r"no (consigo|puedo|logro) (parar|cortar)",
        r"no deja de",
        r"sigue sangrando",
        r"sin parar",
        r"a (chorro|chorros|borbotones)",
        r"(mucha|muchisima|un monton de|demasiada) sangre",
        r"sangr\w* (mucho|muchisimo|sin parar)",
        r"llen[oa] de sangre",
        r"sangre por todas partes",
        r"empapad",
        r"(llevo|lleva) (una|media|dos|tres|varias) hora",
        r"(media|una) hora sangrando",
        r"hemorragia",
        r"cambiado (la gasa|el algodon|de gasa)",
    ),
)

HEAVY_EN = Phrases(
    fires=(
        r"wo ?n ?t stop",
        r"does ?n ?t stop",
        r"(keeps|keep) bleeding",
        r"still bleeding",
        r"(a lot|lots|loads) of blood",
        r"bleed(ing|s)? (quite )?a lot",
        r"heavy bleeding",
        r"bleeding heavily",
        r"(pouring|gushing|streaming|spurting)",
        r"soaked",
        r"(full|filling up) (of|with) blood",
        r"blood everywhere",
        r"can ?t stop (the )?(bleeding|blood)",
        r"for (an|half an|half|two|three|twenty|thirty|forty) (hour|hours|minutes)",
        r"non stop",
        r"(haemorrhag|hemorrhag)",
        r"gone through (a|the|several) (gauze|cotton|towel)",
    ),
)

# Both lists of every signal are always checked, whatever language the call is in.
SIGNALS: dict[str, tuple[Phrases, Phrases]] = {
    "breathing": (BREATHING_ES, BREATHING_EN),
    "swelling": (SWELLING_ES, SWELLING_EN),
    "fever": (FEVER_ES, FEVER_EN),
    "blow": (BLOW_ES, BLOW_EN),
    "trauma_direct": (TRAUMA_DIRECT_ES, TRAUMA_DIRECT_EN),
    "face": (FACE_ES, FACE_EN),
    "injury": (INJURY_ES, INJURY_EN),
    "bleeding": (BLEEDING_ES, BLEEDING_EN),
    "heavy": (HEAVY_ES, HEAVY_EN),
}

_COMPILED = {
    name: (
        tuple(re.compile(p) for phrases in pair for p in phrases.fires),
        tuple(re.compile(p) for phrases in pair for p in phrases.cancels),
    )
    for name, pair in SIGNALS.items()
}


def normalise(text: str) -> str:
    """Lowercase, accent-free text in which only letters, digits and spaces are left."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", stripped).split())


def signals(text: str) -> frozenset[str]:
    """The signals one transcript carries, before they are combined into reasons."""
    normalised = normalise(text)
    found = set()
    for name, (fires, cancels) in _COMPILED.items():
        if any(p.search(normalised) for p in fires) and not any(
            p.search(normalised) for p in cancels
        ):
            found.add(name)
    return frozenset(found)


def match(text: str) -> list[str]:
    """The red flags in one transcript, deduplicated, in the priority order of §6.2."""
    return _reasons(signals(text))


def _reasons(found: frozenset[str]) -> list[str]:
    reasons = []
    if "breathing" in found:
        reasons.append("red_flag_breathing")
    if "swelling" in found and "fever" in found:
        reasons.append("red_flag_swelling_fever")
    if "trauma_direct" in found or ("blow" in found and ("face" in found or "injury" in found)):
        reasons.append("red_flag_trauma")
    if "bleeding" in found and "heavy" in found:
        reasons.append("red_flag_bleeding")
    return reasons


class RedFlagTracker:
    """One per call: the red flags of each transcript, with memory across turns.

    Swelling and fever are remembered, because a caller often gives one in one turn
    ("tengo la cara hinchada") and the other in the next ("y ahora tengo fiebre").
    Pure: it reads no clock and no file. The call plumbing feeds it every final
    caller transcript, echo included, and enforces the playback limits of §6.2.
    """

    def __init__(self) -> None:
        self.swelling = False
        self.fever = False

    def feed(self, text: str) -> list[str]:
        """The red flags of this transcript, read with what earlier turns showed."""
        found = set(signals(text))
        self.swelling = self.swelling or "swelling" in found
        self.fever = self.fever or "fever" in found
        if self.swelling:
            found.add("swelling")
        if self.fever:
            found.add("fever")
        return _reasons(frozenset(found))
