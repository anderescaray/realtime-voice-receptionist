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

Each signal has `fires`, `cancels` and `past` patterns. A `cancels` match removes
only the fires that overlap its own words: a plain negation ("no tengo fiebre", "no
fever") or an unrelated sense of the same word ("golpe de calor", "it hit me that").
So a denial never hides a different, real report in the same sentence ("I didn't
fall, someone punched me in the mouth"). A `past` match ("hace anos", "years ago")
removes the signal's fires after it (or a few words before it) in its own clause
only; a clause ends at punctuation or at a word of contrast, time or a new event
("pero", "y", "now", "hoy"). A cancel never runs across punctuation, and one that
is itself negated ("no me ha dejado de sangrar") cancels nothing. Playback limits
and when to run the matcher are the call plumbing's job, not this module's.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from dataclasses import dataclass
from typing import NamedTuple

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
    past: tuple[str, ...] = ()


# --- trouble breathing -----------------------------------------------------

BREATHING_ES = Phrases(
    fires=(
        r"no (puedo|puede|podia|podemos|consigo|consigue|logro|logra) (\w+ ){0,2}respirar",
        r"(me|le|nos) cuesta (\w+ ){0,3}respirar",
        r"cuesta (mucho |muchisimo |bastante |un monton |un poco )?respirar",
        r"respirar (me |le |nos )?cuesta",
        r"dificultad(es)? (para|al|en|con) respirar",
        r"problemas? (para|al|con) respirar",
        r"(es |esta siendo |resulta )?(muy |super |bastante )?dificil respirar",
        r"(apenas|casi no) (puedo|puede|podia|consigo|consigue) (\w+ ){0,2}respirar",
        r"respir\w* (muy |bastante |super )?(mal|fatal|con (mucha |bastante )?dificultad)",
        r"no respira bien",
        r"respira con dificultad",
        r"(me|le|nos) falta (el )?aire",
        r"falta de aire",
        r"no (me|le|nos) (llega|entra|pasa) (bien )?(el |suficiente |bastante )?aire",
        r"(me|se) (quedo|queda|estoy quedando|esta quedando) sin aire",
        r"sin (casi |apenas )?aire",
        r"cuesta (mucho |muchisimo )?(coger|tomar|pillar|meter|que entre) (el )?aire",
        r"no (puedo|puede|consigo|consigue) (coger|tomar|meter) (bien )?(el )?aire",
        r"\bah?og(o|a|as|an|ando|andose|andome|ada|ado)\b",
        r"(me|se) asfixi",
        r"asfixi(ando|andome|andose|ad[oa])",
        r"atragant(and|ad[oa])",
        r"(se me|se le|se) (esta cerrando|cierra|ha cerrado) la garganta",
        r"(tengo |tiene )?la garganta (muy |casi |totalmente )?cerrada",
        r"(me|le) cuesta (mucho |muchisimo |horrores |un monton )?tragar",
        r"tragar (me |le )?cuesta",
        r"no (puedo|puede|consigo|consigue) (ni )?(tragar|pasar saliva)",
        r"no (puedo|puede) (ni )?hablar bien de",
        r"pit[oa] al respirar",
        r"\b(no|casi no|apenas) respir(o|a|amos|an)\b",
        r"(falta|cuesta) (de )?(la )?respiracion",
        r"(problemas?|dificultad(es)?) (de|con|para|en) (la )?respiracion",
        r"respiracion (\w+ )?(dificil|mala|fatal|rara|entrecortada)",
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
        r"\b(can ?t|cannot|could ?n ?t|unable to) (\w+ ){0,2}breathe?\b",
        r"\bbreathless",
        r"\b(is ?n ?t|is not|not|stopped|stops) breathing",
        r"throat (\w+ ){0,2}(tight|tightening|blocked|closing)",
        r"blocking (my|his|her|the) (throat|airway)",
        r"(trouble|difficulty|difficulties|problems?|issue|issues) (with )?breathing",
        r"(hard|difficult|struggling|struggle|struggles|tough|painful) to breathe",
        r"(hard|difficult) time (breathing|getting (\w+ )?air)",
        r"(short of|shortness of|out of|running out of|lack of) (breath|air)",
        r"(can ?t|cannot|could ?n ?t|unable to) (get|catch|draw|take) (\w+ ){0,3}(breath|air)",
        r"(hardly|barely) (get|catch|draw|take) (\w+ ){0,3}(breath|air)",
        r"(struggling|struggle|trying|fighting) to (get|catch|take) (\w+ ){0,2}(air|breath)",
        r"(hard|difficult) to (get|catch|draw|take) (\w+ ){0,2}(air|breath)",
        r"(fighting|struggling|gasping) for (breath|air)",
        r"not getting (enough )?air",
        r"gasping",
        r"\b(choking|chocking|choke|chokes|suffocating|suffocate|wheezing|wheeze)\b",
        r"(my|his|her|the) throat (\w+ ){0,2}(closing|closed|swelling shut|swollen shut)",
        r"throat (is |feels |keeps )?(closing|swelling|tightening) (up|shut)",
        r"(can ?t|cannot|could ?n ?t|unable to) swallow",
        r"(struggling|struggle|hard|difficult|trouble|difficulty) (to swallow|swallowing)",
        r"(my|his|her|the) airway",
        r"breathing (is|was) (really |very |quite )?(bad|difficult|hard|rough|laboured|labored)",
        r"breathing (is|feels|sounds) (really |very )?(shallow|heavy|tight)",
        r"can (hardly|barely) (breathe|get (\w+ )?air|swallow)",
        r"(hardly|barely) able to (breathe|get (\w+ )?air|swallow)",
        r"breathing funny",
    ),
    cancels=(
        r"no (trouble|difficulty|problem|problems) breathing",
        r"not (struggling|struggle) to breathe",
        r"breathing (is )?(fine|ok|okay|normal|normally)",
        r"can breathe (fine|ok|okay|normally)",
    ),
)

# --- swelling (one half of swelling with fever) -----------------------------

SWELLING_ES = Phrases(
    fires=(
        r"hinch(ad|azon|and|a|o)",
        r"inflam(ad[oa]|acion|and|a|o)",
        r"(flemon|absceso|abceso|abseso)",
        r"\b(un|una) (bola|bolita|bulto|bultito)\b",
        r"bulto (\w+ ){0,2}(en|debajo|por) (la|el)",
        r"bulto (muy |super |bastante )?(grande|enorme|gigante|gordo|duro)",
        r"como un(a)? (globo|huevo|pelota|melon|tomate|balon|pomelo|naranja|bollo)",
        r"(cara|mejilla|carrillo|moflete|mandibula) (\w+ ){0,2}(gord[oa]|grande|enorme|gigante)",
        r"(cara|mejilla|carrillo|moflete|mandibula) (\w+ ){0,2}(deformad[oa]|inflad[oa])",
        r"el doble de (grande|tamano)",
        r"se (me|le) ha puesto la (cara|mejilla|mandibula)",
    ),
    cancels=(
        r"no (tengo|tiene|hay) (nada de )?(hinchazon|inflamacion|flemon|bulto)",
        r"sin (hinchazon|inflamacion|flemon)",
        r"no (esta|lo tengo|la tengo|lo tiene|la tiene) (nada )?(hinchad[oa]|inflamad[oa])",
        r"no (se me|se le) (ha )?(hinch|inflam)",
        r"nada (hinchad[oa]|inflamad[oa])",
    ),
)

SWELLING_EN = Phrases(
    fires=(
        r"swo?llen",
        r"swell(ing|s|ed)?",
        r"puff(y|ed|ing)",
        r"blow(n|ing) up",
        r"(abscess|absess|abcess|gumboil)",
        r"inflam(ed|mation)",
        r"\b(a|big|hard|huge|massive|large|enormous|painful) (lump|bump|knot|mass)\b",
        r"balloon(ed|ing)",
        r"size of a(n)? (golf ball|tennis ball|egg|plum|orange|walnut|fist)",
        r"(twice|double|triple) (the|its|normal) size",
        r"(face|cheek|jaw|chin|gum|lip|neck) (\w+ ){0,2}(huge|massive|enormous|giant|bulging)",
        r"(face|cheek|jaw|chin|lip|mouth) (\w+ ){0,2}(blown up|lopsided|distorted)",
        r"(huge|massive|enormous|giant|bulging) (cheek|face|jaw|gum|lip|lump)",
    ),
    cancels=(
        r"no (swelling|lump|abscess|inflammation)",
        r"(not|is ?n ?t|was ?n ?t) (swollen|puffy|inflamed)",
        r"nothing (is )?swollen",
        r"no sign of swelling",
    ),
)

# --- fever (the other half) -------------------------------------------------

FEVER_ES = Phrases(
    fires=(
        r"fiebre",
        r"febril",
        r"febricula",
        r"decimas",
        r"destemplad[oa]",
        r"destemplanza",
        r"tiritando",
        r"tiritona",
        r"escalofrios",
        r"calentura",
        r"(tengo|tiene|con|subido la|subio la) (mucha |algo de |alta )?temperatura",
        r"temperatura (alta|elevada)",
        r"(3[789]|4[01]) (de |y )?(fiebre|temperatura|grados|y medio)",
        r"\b(3[789]|40) [0-9]\b",
        r"\b(tengo|tiene|estoy con|esta con|a) (3[89]|40)\b(?! (anos|euros|minutos|kilos))",
        r"treinta y (siete|ocho|nueve)",
        r"cuarenta (de fiebre|grados)",
        r"(me|le) arde (la cabeza|la frente|la cara|el cuerpo)",
        r"ardiendo( en fiebre| de fiebre)?",
        r"caliente al (tacto|tocar\w*)",
        r"(quema|quemando) (mucho |un monton )?(al tocar\w*|la frente|la cabeza|de fiebre)",
    ),
    cancels=(
        r"no (tengo|tiene|hay|he tenido|ha tenido) (nada de )?(fiebre|temperatura|decimas)",
        r"no (tengo|tiene|hay) (nada de )?calentura",
        r"sin (nada de )?(fiebre|temperatura|decimas|calentura)",
        r"nada de fiebre",
        r"fiebre no (tengo|tiene|he tenido)",
        r"no (me|le) ha dado fiebre",
        r"(la )?temperatura (esta|la tengo|la tiene) (bien|normal)",
    ),
)

FEVER_EN = Phrases(
    fires=(
        r"fever(ish)?",
        r"temperature",
        r"(high|running a|got a|has a|have a|had a|with a) temp\b",
        r"temp (of|is|was|hit|reached)",
        r"shiver(ing|s|y)",
        r"chills",
        r"hot and cold",
        r"burning up",
        r"(hot|burning|boiling) to (the )?touch",
        r"(forehead|head|skin|body) (is |feels )?(boiling|burning|roasting|red hot)",
        r"(she|he|kid|son|daughter|baby|child) (is |feels )?(boiling|burning|roasting|red hot)",
        r"thirty (seven|eight|nine)",
        r"forty degrees",
        r"(3[789]|4[01])(\s+[0-9])? (degrees|point|celsius)",
        r"(hundred|a hundred|one hundred) and (one|two|three|four)",
        r"\b10[1-4] (degrees|fahrenheit)\b",
    ),
    cancels=(
        r"no (fever|temperature|high temperature|high temp)",
        r"not? got (a |any )?(fever|temperature)",
        r"have ?n ?t got (a |any )?(fever|temperature)",
        r"do ?n ?t have (a |any )?(fever|temperature)",
        r"does ?n ?t have (a |any )?(fever|temperature)",
        r"(temperature|temp) (is|was) (normal|fine|ok|okay)",
        r"without a (fever|temperature)",
        r"not feverish",
        r"no sign of (a )?(fever|temperature)",
        r"sensitive to (hot|cold)",
    ),
)

# --- a blow or an accident to the face or mouth ------------------------------
# A blow on its own is not enough: it needs a link to the face, the mouth, the
# teeth or the jaw (FACE_*), or a clear injury (INJURY_*). The direct patterns
# already carry that link. The cancels hold the senses that are not an injury now:
# a blow long ago, "de golpe" (suddenly), "it hit me that", a filling that fell out,
# something done "by accident", or an explicit negation ("ningun golpe", "no knock").

BLOW_ES = Phrases(
    fires=(
        r"(?<!de )golpe",
        r"golpead[oa]",
        r"(me|se|te) golpe",
        r"\b(me|se|te|nos) (he |ha |has |hemos )?(cai|caido|caida|cayo|caimos)\b",
        r"(me|se|te) (he|ha|has) dado (un|una) (golpe|tortazo|porrazo|trompazo|cabezazo|mamporro)",
        r"(me|se|te) (he |ha |has )?(dado|pegado|chocado|tragado|comido) (de morros|con |contra )",
        r"(tragado|comido) (la|el|una|un) (esquina|puerta|farola|pared|columna|canto|marco)",
        r"\b(me|se|te) (di|dio|pego) (con |contra )(un|una|el|la|los|las)",
        r"(le|me|nos) (han|ha|dieron|pegaron) (dado )?(un|una) (golpe|pelotazo|balonazo|codazo)",
        r"(le|me|nos) (han|ha|dieron|pegaron) (dado |pegado )?con (un|una|el|la)",
        r"caida",
        r"\baccidente",
        r"atropell",
        r"\b(choque|chocado|choco|chocar)\b",
        r"\b(pinazo|trastazo|castanazo|lenazo|guantazo|ostion|hostiazo|bofet\w*|paliza|pelea)\b",
        r"\b(una|un|la|vaya|menuda|tremenda) (leche|hostia|ostia|torta|galleta)\b",
        r"\btrope(z|c)\w*",
        r"\bacab\w* de (caer\w*|golpear\w*|dar\w* (un|una|con|contra))",
        r"\bcaer(se|me|le|te|nos)\b",
        r"\b(me|le|nos|te) (pegaron|pego|han pegado|ha pegado)\b",
        r"\b(me|le|se|te) (dio|dieron|di) (un|una) (?!pinchazo|latigazo)\w+azo\b",
        r"(patada|codazo|punetazo|pelotazo|balonazo|trompazo|porrazo|rodillazo)",
        r"(cabezazo|mamporro|golpetazo|raquetazo|palazo|portazo)",
        r"(me|se) (he|ha) estampado",
        r"de morros",
        r"(me|se) (he|ha) (estrellado|empotrado)",
        r"resbal(e|o|ado)",
        r"traumatismo",
        r"tortazo",
    ),
    cancels=(
        r"golpe de (calor|suerte|estado)",
        r"se (me|le|te) (ha )?(caido|cayo) (un|una|el|la) (empaste|corona|funda|puente)",
        r"se (me|le|te) (ha )?(caido|cayo) (un|una|el|la) (provisional|diente de leche)",
        r"sin (ningun |darse |darme |haber tenido )?(golpes?|caidas?|accidentes?|traumatismos?)",
        r"(ningun|ni un) (golpe|accidente|traumatismo|caida)",
        r"no (me di|se dio|me he dado|se ha dado) (ningun |un )?(golpe|porrazo|trompazo)",
        r"no (hubo|ha habido|tuve|tuvo|fue por) (ningun |ninguna |una? )?(golpe|caida|accidente)",
        r"no (me |se )?(golpee|golpeo|he golpeado|ha golpeado|cai|cayo|he caido|ha caido)",
        r"\bni (un |una |ningun |ninguna )?(golpe|caida|accidente|porrazo|traumatismo)\b",
    ),
    past=(
        r"hace (\w+ ){0,3}(anos|ano|meses|mes|semanas|semana)",
        r"(anos|meses) atras",
        r"el (ano|mes) pasado",
        r"de (pequen[oa]|nin[oa]|joven|chaval|adolescente)\b",
        r"cuando era (nin[oa]|peque|pequen[oa]|joven|chaval|adolescente)",
        r"en la infancia",
        r"hace (mucho |bastante )?tiempo",
        r"nada reciente",
    ),
)

BLOW_EN = Phrases(
    fires=(
        r"(got|was|were|been|just) hit",
        r"hit (me|him|her|them|my|his|their) (in|on|with|right|straight)",
        r"hit (in|on|by|with) (the|a|an)",
        r"hit (my|his|her) (face|mouth|tooth|teeth|jaw|lip|chin|nose|head|cheek)",
        r"((foot|basket|base)?ball|puck|bat|stick) (hit|smacked|whacked|struck|caught)",
        r"(door|gate|cupboard|table|counter|swing|car|kerb|curb) (hit|smacked|whacked|banged)",
        r"(elbow|fist|knee|head) (hit|smacked|whacked|struck|caught|banged)",
        r"(got|took|caught) (a |an |the )?(\w+ )?((foot|basket|base)?ball|puck|bat|stick)",
        r"(got|took|caught) (a |an |the )?(\w+ )?(elbow|knee|fist|punch|kick|door|gate|corner)",
        r"hit the (floor|ground|pavement|kerb|curb|wall|table|counter|door|edge|step|corner)",
        r"\bknocked\b(?! on wood)",
        r"\bfell\b(?! asleep| for)",
        r"\b(a|the|his|her|my|after a|bad) fall\b",
        r"\bslipped\b",
        r"\bbump(ed)? (my|his|her|their|into)\b",
        r"\bhit (him|her|me|them|us)\b",
        r"\b(in|into|got in|got into) a fight\b",
        r"\bface ?plant",
        r"\bbeaten up\b",
        r"(fell|fallen|falling) (off|over|down|on|in|into|onto|out of)",
        r"had a fall",
        r"took a (tumble|fall)",
        r"came off (my|his|her|the) (bike|scooter|skateboard|horse)",
        r"\btripped\b",
        r"\bwent flying\b",
        r"(walked|ran|bumped|crashed|smashed|went) (straight |right )?into (a|an|the|my|his|her)",
        r"\baccident\b",
        r"(car crash|crashed|collided)",
        r"smashed (my|his|her|into|the)",
        r"(punched|elbowed|headbutted|headbutt|kicked|whacked|smacked|clobbered|bashed|clipped)",
        r"banged (my|his|her|it|the)",
        r"struck (in|on|by|with) (the|a|an)",
        r"(rugby )?tackled",
        r"over the handlebars",
        r"face (planted|first)",
        r"(blow|knock|hit|smack|whack|bang) to (the|my|his|her)",
    ),
    cancels=(
        r"\bit hit me (that|how|what)\b",
        r"hit a nerve",
        r"(filling|crown|cap|bridge|veneer) (has |had |just |already )*"
        r"(fell|fall|falls|came|come|comes) (out|off)",
        r"\bno (any |a )?(knock|blow|hit|fall|accident|trauma|impact|crash)"
        r"( (or|nor) (a |any )?(knock|blow|hit|fall|accident|trauma|impact|crash))?\b",
        r"without (a |any )?(knock|blow|hit|fall|accident|trauma|impact)",
        r"not (from |after |because of |due to )?(a |any )?(knock|blow|hit|fall|accident|trauma)",
        r"(did ?n ?t|did not|have ?n ?t|has ?n ?t|never) (fall|hit|knock|bang|bump|crash)",
        r"(was ?n ?t|was not|were ?n ?t) (hit|knocked|punched|in an accident)",
    ),
    past=(
        r"(\w+ )?(years?|months?|weeks?) ago",
        r"(last|previous) (year|month)",
        r"back in (school|college|uni|the day|\d{4})",
        r"when (i|he|she) was (a )?(kid|child|young|younger|little|boy|girl|teenager)\b",
        r"as a (kid|child|teenager|youngster)",
        r"(a )?long time ago",
        r"nothing recent",
    ),
)

# Direct patterns: a blow that already names the face, the mouth or the accident.
TRAUMA_DIRECT_ES = Phrases(
    fires=(
        r"golpe en (la|el|los|las)",
        r"accidente de (coche|moto|bici|bicicleta|trafico|patinete)",
        r"de morros",
        r"(me|se) (he|ha) caido de (la|el|las|los)",
        r"caida de (la|el) (bici(cleta)?|moto|escalera|silla|cama|columpio|patinete)",
        r"(le|me) (ha|han|dieron) (dado )?(un|una) (pelotazo|balonazo|codazo|punetazo|cabezazo) en",
        r"(pelota|balon|bola|puerta|esquina) en (toda )?(el|las?|los) (cara|boca|mandibula|nariz)",
    ),
    cancels=BLOW_ES.cancels,
    past=BLOW_ES.past,
)

TRAUMA_DIRECT_EN = Phrases(
    fires=(
        r"hit (in|on) the (face|mouth|jaw|teeth|tooth|lip|chin|nose|head|cheek)",
        r"(blow|knock|hit|smack|whack) to (the|my|his|her) (face|mouth|jaw|head|chin|teeth|nose)",
        r"knocked (my|his|her|a|the) (tooth|teeth|front tooth|front teeth) (out|loose|back|in)",
        r"car (accident|crash)",
        r"(fell|came) off (my|his|her|the) (bike|scooter|skateboard|ladder|horse)",
        r"fell (down|off) the (stairs|steps|ladder)",
        r"(punched|elbowed|kicked|smacked|whacked) in (the|my|his|her) (face|mouth|jaw|nose|teeth)",
        r"face (planted|first) (on|into|in)",
    ),
    cancels=BLOW_EN.cancels,
    past=BLOW_EN.past,
)

FACE_ES = Phrases(
    fires=(
        r"\b(cara|boca|mejillas?|carrillos?|mofletes?|pomulos?|barbilla|menton|nariz|labios?)\b",
        r"\b(dientes?|muelas?|dentadura|incisivos?|colmillos?|paletas?)\b",
        r"\b(mandibula|encias?|paladar|lengua|maxilar)\b",
        r"(los|las) dos de delante",
    ),
)

FACE_EN = Phrases(
    fires=(
        r"\b(face|mouth|cheeks?|chin|jaws?|nose|lips?)\b",
        r"\b(tooth|teeth|molars?|incisors?|dentures?|braces)(?!brush|paste|pick)",
        r"\b(gums?|palate|tongue)\b",
    ),
)

INJURY_ES = Phrases(
    fires=(
        r"sangr",
        r"herida",
        r"\b(rot[oa]s?|rompi|rompio|partid[oa]s?|parti|partio|astillad[oa]|astillo|rajad[oa]|quebrad[oa])\b",
        r"se (me|le) (mueve|ha movido|movio|salio|ha salido)",
        r"(suelto|suelta|flojo|floja|bailando|baila)",
        r"(torcido|torcida|torcio|doblado|doblada|desplazado|desplazada|hundido|hundida)",
        r"(metido|metida) (\w+ ){0,2}(para dentro|hacia dentro)",
        r"fuera de (su )?sitio",
        r"(saltado|volado) (un|el|medio) diente",
        r"(me|le) duele",
        r"dolor",
        r"hinch(ad|azon)",
        r"(me|le) falta (un|medio) (diente|trozo)",
    ),
)

INJURY_EN = Phrases(
    fires=(
        r"(blood|bleed|bleeding|bled)",
        r"\b(cut|split|busted|gash)\b",
        r"(broken|broke|chipped|cracked|snapped|shattered|smashed)",
        r"(loose|wobbly|wiggling|wiggly|moving|shifted|displaced|dislodged)",
        r"(crooked|bent|twisted|pushed (back|in|sideways|out))",
        r"(out of (place|line|position)|do ?n ?t line up)",
        r"knocked (out|loose|back|in|crooked)",
        r"(hurts|hurt|hurting|pain|painful|sore)",
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
        r"\bcoagul",
        r"\b(gasa|gasas|algodon|algodones)\b",
    ),
    cancels=(
        r"no (me |le |te )?sangra",
        r"no (esta|estaba) sangrando",
        r"(ha|he|han) (parado|dejado) de sangrar",
        r"se (ha|le ha|me ha) cortado (la sangre|el sangrado)",
        r"no hay sangre",
        r"sin sangre",
        r"no sangro",
        r"ya no (me |le )?(sale sangre|sangra)",
        r"ya (ha |se ha )?coagulad[oa]",
    ),
)

BLEEDING_EN = Phrases(
    fires=(
        r"(bleed|bled)",
        r"blood",
        r"(haemorrhag|hemorrhag)",
        r"\bclot(s|ted|ting)?\b",
        r"(gauze|cotton pad|cotton pads|cotton wool|cotton roll|cotton balls?|swab|swabs)",
    ),
    cancels=(
        r"not bleeding",
        r"is ?n ?t bleeding",
        r"was ?n ?t bleeding",
        r"no (blood|bleeding)",
        r"stopped bleeding",
        r"blood (thinners?|pressure|tests?|sugar|type|donor|donation|work)",
        r"bleeding (has |had )?(finally |already |now )?stopped",
        r"does ?n ?t bleed",
        r"did ?n ?t bleed",
        r"(has |already |it s )clotted",
    ),
)

HEAVY_ES = Phrases(
    fires=(
        r"\bno (se )?(me |le |nos )*"
        r"(para|paraba|paro|corta|cortaba|corto|detiene|detenia|frena|frenaba|cesa|cesaba)\b"
        r"(?! (manana|hoy|el|la|las|los|que|mi|ti|una|un|ir|cuando|nada)\b)",
        r"no (consigo|puedo|logro|podemos) (parar\w*|cortar\w*|detener\w*|que pare|que se corte)",
        r"no hay (manera|forma|modo) de (que (pare|se pare|se corte)|parar\w*|cortar\w*)",
        r"imposible (parar\w*|cortar\w*|detener\w*|que pare|que se corte)",
        r"nada (lo |la )?(para|corta|detiene|frena)",
        r"no (deja|para|cesa) de",
        r"\b(no|nunca) (se )?(me |le |te )?(paro|paraba|dejo|dejaba|ceso|cesaba) de",
        r"\bno (se )?(me |le |nos )*corta (la sangre|el sangrado)",
        r"que (deje|pare) de sangrar",
        r"\b(no|nunca|todavia no|aun no|tampoco) (me |le |te |se )?(ha|he|han) (parado|dejado) de",
        r"sangr\w* (un monton|una barbaridad|a lo bestia|muchisimo|a saco)",
        r"(todavia|aun) sangr",
        r"sangra y sangra",
        r"(todavia|aun) no (ha parado|se ha parado|se ha cortado|para|corta)",
        r"(sigo|sigue|siguen|segu(imos|ia|ian|iamos)) "
        r"(sangrando|echando sangre|saliendo|sin cortarse|sin parar)",
        r"sigue sin (parar|cortarse|detenerse|coagular)",
        r"no (va a menos|disminuye|baja|afloja|cede|remite)",
        r"(vuelto|vuelve|volvio) a sangrar",
        r"sangra(ndo)? (otra vez|de nuevo)",
        r"sin (parar|cesar|descanso|cortarse|detenerse|coagular)",
        r"(de continuo|continuamente|constantemente|constante|ininterrumpid\w*)",
        r"no (se )?(me |le )?coagula",
        r"no (ha|habia|han|habian) coagulado",
        r"\bno (se )?(me |le |nos )*"
        r"(para|paraba|paro|corta|cortaba|corto|detiene|detenia|frena|frenaba|cesa|cesaba) "
        r"(la sangre|el sangrado|la hemorragia)",
        r"a (chorro|chorros|borbotones|mares|raudales)",
        r"(mucha|muchisima|un monton de|demasiada|abundante) sangre",
        r"bocanadas de sangre",
        r"sangr\w* (mucho|muchisimo|abundante\w*|demasiado|sin parar|sin descanso|de continuo)",
        r"llen(a|o|ando|andose) (\w+ ){0,3}de sangre",
        r"sangre por todas partes",
        r"empap(ad|a|an|ando)",
        r"chorreando",
        r"hemorragia",
        r"(cambiado|cambiando|gastado|usado|llenado) (\w+ ){0,3}(gasa|gasas|algodon|algodones)",
        r"(varias|muchas|tres|cuatro|cinco|seis|\d+) (gasas|algodones)",
    ),
)

HEAVY_EN = Phrases(
    fires=(
        r"(wo ?n ?t|will not|does ?n ?t|does not|would ?n ?t|would not|did ?n ?t|did not) "
        r"(\w+ ){0,2}(to )?(stop|slow down|clot|ease|let up)\b",
        r"(do ?n ?t think|not sure|do ?n ?t know if) (\w+ ){0,3}(stopped|clotted)",
        r"(is ?n ?t|is not|can ?t|cannot) (stopping|slowing down|clotting|stop|slow down|clot)",
        r"refuses to (stop|clot|slow down)",
        r"\b(not|is ?n ?t|is not|never) slowing\b",
        r"(bleeding|blood) (\w+ ){0,2}(getting|got|gets) (worse|heavier)",
        r"\b(never|not|has ?n ?t|did ?n ?t|has not|did not) (\w+ ){0,2}stopped\b",
        r"(not|never) (stopping|slowing down|slowing|clotting|easing|letting up)",
        r"(has ?n ?t|have ?n ?t|has not|have not) (stopped|slowed down|clotted|eased)",
        r"still (has ?n ?t|have ?n ?t|has not|have not|is ?n ?t|not) (stopped|stopping|clotted)",
        r"(keeps|keep|kept) (on )?(bleeding|pouring|gushing|flowing|oozing|coming|filling|soaking)",
        r"still (bleeding|pouring|gushing|flowing|oozing|coming|going)",
        r"(started |starts |begun )?bleeding (all over )?again",
        r"bleeding started (up )?again",
        r"(a lot|lots|loads|tons|pools|mouthfuls|so much|too much) of blood",
        r"bleed(ing|s)? (quite |really |very |so )?(a lot|heavily|badly|profusely|freely)",
        r"(heavy|profuse|severe|excessive|uncontrolled) bleeding",
        r"(pouring|gushing|streaming|spurting|pumping|flooding|oozing)",
        r"soak(ed|ing|s)",
        r"drench(ed|ing)",
        r"saturat(ed|ing)",
        r"(full|filling|fills|filled) (\w+ ){0,3}(of|with) (fresh )?blood",
        r"blood (\w+ ){0,2}fill(ing|s) (up )?(my|his|her|the) mouth",
        r"fill(ing|s) (up )?(my|his|her|the) mouth",
        r"blood everywhere",
        r"\b(can ?t|cannot|could ?n ?t|unable to) (\w+ ){0,2}(to )?(get it to |make it )?"
        r"stop (it|the bleeding|the blood|bleeding)\b",
        r"\b(can ?t|cannot|could ?n ?t|unable to) (get it to|make it) stop\b",
        r"\b(get|make) (the bleeding|the blood|it) (to )?stop\b",
        r"bleed\w* (really |so |very )?(bad|badly|loads|like crazy|like mad|like hell)",
        r"(so much|lots of|loads of) (blood|bleeding)",
        r"(not|wo ?n ?t|will not|does ?n ?t|is ?n ?t) (\w+ ){0,2}(clot|clotting)",
        r"no way to (make it |get it to )?stop",
        r"nothing (is )?(stopping|helps|helping|works|working)",
        r"no matter how (hard|long|much) (i|we|he|she) (press|bite|hold)",
        r"(held|holding|pressed|pressing) (\w+ ){0,3}for ages",
        r"non ?stop",
        r"(constant|constantly|continual|continually|continuous|continuously)",
        r"(will not|wo ?n ?t|does ?n ?t|did ?n ?t) form a clot",
        r"(haemorrhag|hemorrhag)",
        r"(gone|going|went) through (\w+ ){0,3}(gauze|cotton|pad|pads|tissue|towel|pack|swab|roll)",
        r"(chang(ing|ed)|replace|replacing|swapp(ing|ed)) (the |my )?(gauze|cotton|pad|pads|swab)",
    ),
)

# A duration counts towards bleeding that will not stop only next to a mention of
# bleeding in the same transcript: "since yesterday" or "all day" on its own is
# ordinary talk ("I'm at work all day Thursday").
DURATION_ES = Phrases(
    fires=(
        r"(desde|toda) (ayer|anoche|esta manana|la manana|la tarde|la noche)",
        r"todo el dia",
        r"(llevo|lleva|llevamos) (ya |casi |mas de )?(una|media|dos|tres|varias|\d+) (hora|horas)",
        r"(media|una|dos|tres|varias|\d+) (hora|horas) (sangrando|apretando)",
        r"(despues|mas|cerca) de (mas de |casi )?(media|una|dos|tres|varias|\d+) (hora|horas)",
        r"desde hace (\w+ ){0,3}(horas?|dias?)\b",
        r"\bhace (\w+ ){1,3}(horas?|dias?) que",
        r"\blleva\w* sangrando (\w+ ){0,3}(horas?|dias?)\b",
        r"\b(llevo|lleva|llevamos) (\w+ ){0,3}(dias?)\b",
        r"desde que (me|le|nos|te) (sacaron|quitaron|operaron|hicieron)",
        r"a los (pocos|cinco|diez|dos) minutos",
        r"cada (pocos |dos |tres |cinco )?minutos",
    ),
)

DURATION_EN = Phrases(
    fires=(
        r"since (yesterday|last night|this morning|lunch|breakfast)",
        r"all (day|night|morning|afternoon|evening)",
        r"for (hours|ages|days)",
        r"\bfor (over |more than |nearly |almost |about |around |like |the last |the past )*"
        r"((an|a|one|two|three|four|five|six|several|a few|a couple of|couple of|\d+) )?"
        r"(hours?|days?)\b",
        r"since (the|my|his|her) (extraction|surgery|op|operation|procedure)",
        r"for (over |more than |nearly |almost )?"
        r"(an|a|one|half an|half|two|three|four) (hour|hours)",
        r"for (over |nearly |almost |about |around |like )*"
        r"(twenty|thirty|forty|forty five|fifty|\d+) (minutes|mins)",
        r"(been|after|over|nearly|almost) (an|a|one|half an|two|three|four) (hour|hours)",
        r"(been|after|over|nearly) (twenty|thirty|forty|forty five|\d+) (minutes|mins)",
        r"every (few |couple of |two |three |five |\d+ )?minutes",
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
    "duration": (DURATION_ES, DURATION_EN),
}


def _at_word_start(pattern: str) -> str:
    """Cancels only match from the start of a word, so "nino se cayo" holds no "no se"."""
    return pattern if pattern.startswith(("\\b", "(?<")) else "\\b" + pattern


_Compiled = tuple[tuple[re.Pattern[str], ...], ...]

_COMPILED: dict[str, _Compiled] = {
    name: (
        tuple(re.compile(p) for phrases in pair for p in phrases.fires),
        tuple(re.compile(_at_word_start(p)) for phrases in pair for p in phrases.cancels),
        tuple(re.compile(_at_word_start(p)) for phrases in pair for p in phrases.past),
    )
    for name, pair in SIGNALS.items()
}

# Words that negate what follows. A cancel right after one of them is itself negated
# ("no me ha dejado de sangrar", "it never really stopped bleeding", "isnt breathing
# normally"), so it cancels nothing.
_NEGATORS = frozenset(
    [
        "no",
        "ni",
        "nunca",
        "tampoco",
        "jamas",
        "apenas",
        "not",
        "never",
        "t",
        "hardly",
        "barely",
        "isnt",
        "wasnt",
        "werent",
        "hasnt",
        "havent",
        "hadnt",
        "didnt",
        "doesnt",
        "dont",
        "cant",
        "cannot",
        "couldnt",
        "wont",
        "wouldnt",
    ]
)
# Words that may stand between the negator and the cancel: "no ME ha dejado",
# "never REALLY stopped".
_BETWEEN = frozenset(
    [
        "me",
        "le",
        "te",
        "se",
        "nos",
        "lo",
        "la",
        "ha",
        "he",
        "han",
        "has",
        "have",
        "had",
        "really",
        "even",
        "yet",
        "ever",
        "still",
        "is",
        "it",
        "s",
    ]
)
# A cancel that holds one of these words is already a denial ("no tengo fiebre",
# "dont have a fever", "sin golpe"); a word before it never voids it.
_DENIAL_WORDS = _NEGATORS | frozenset(
    [
        "sin",
        "ningun",
        "ninguna",
        "nada",
        "without",
        "don",
        "doesn",
        "didn",
        "haven",
        "hasn",
        "isn",
        "wasn",
        "weren",
        "nothing",
    ]
)
# Words saying a symptom is fine or over. Only a cancel holding one of them can be
# negated by the words before it ("no ha PARADO de sangrar"); any other cancel, such
# as a denial or another sense of a word ("se me ha caido un empaste"), always holds.
_STATUS_WORDS = frozenset(
    [
        "parado",
        "dejado",
        "cortado",
        "coagulado",
        "coagulada",
        "stopped",
        "clotted",
        "normal",
        "normally",
        "fine",
        "ok",
        "okay",
        "bien",
        "perfectamente",
    ]
)
# A denial or an end: only these make the tracker forget a remembered part.
_FORGET_WORDS = _DENIAL_WORDS | _STATUS_WORDS

# Where a clause ends, for the `past` cancels: punctuation in the raw transcript, and
# these words in the normalised text (a contrast, a change of time, a new event).
_PUNCTUATION_END = re.compile(r"[,.;:!?]+(?=\s|$)")
_CLAUSE_WORDS = re.compile(
    r"\b(pero|aunque|sin embargo|ahora|hoy|esta manana|esta tarde|esta noche|anoche|ayer"
    r"|y|e|acabo de|acaba de|but|though|although|however|now|today|this morning"
    r"|this afternoon|tonight|last night|yesterday|and|just)\b"
)
# A clause with one of these is about now, so a `past` phrase in it covers nothing.
_PRESENT = re.compile(
    r"\b(hoy|ahora|esta manana|esta tarde|esta noche|acabo de|acaba de|hace un rato"
    r"|today|now|this morning|this afternoon|tonight|just)\b"
)


def normalise(text: str) -> str:
    """Lowercase, accent-free text in which only letters, digits and spaces are left."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", stripped).split())


def _normalise_with_clauses(text: str) -> tuple[str, list[int], list[int]]:
    """The normalised text, where its clauses start, and where its punctuated parts start."""
    normalised = ""
    punctuated = [0]
    for part in (normalise(part) for part in _PUNCTUATION_END.split(text)):
        if not part:
            continue
        if normalised:
            normalised += " "
            punctuated.append(len(normalised))
        normalised += part
    starts = punctuated + [m.start() for m in _CLAUSE_WORDS.finditer(normalised)]
    return normalised, sorted(set(starts)), punctuated


def _clause(starts: list[int], pos: int) -> int:
    """The index of the clause that holds offset `pos`."""
    return bisect.bisect_right(starts, pos) - 1


def _clause_text(normalised: str, starts: list[int], index: int) -> str:
    end = starts[index + 1] if index + 1 < len(starts) else len(normalised)
    return normalised[starts[index] : end]


def _negated(normalised: str, start: int) -> bool:
    before = normalised[:start].split()
    if before and before[-1] in _NEGATORS:
        return True
    return len(before) >= 2 and before[-2] in _NEGATORS and before[-1] in _BETWEEN


class _Cancel(NamedTuple):
    start: int
    end: int
    forgets: bool  # a denial or an end: the tracker forgets the part


def _live_cancels(
    patterns: tuple[re.Pattern[str], ...], normalised: str, punctuated: list[int]
) -> list[_Cancel]:
    """Each cancel that applies: not negated, and not running across punctuation.

    "No, me cai por las escaleras" holds "no me cai" only once the comma is gone, so a
    cancel that spans the start of a punctuated part cancels nothing.
    """
    spans = []
    for pattern in patterns:
        for m in pattern.finditer(normalised):
            if any(m.start() < p < m.end() for p in punctuated):
                continue
            words = set(m.group(0).split())
            guarded = words & _STATUS_WORDS and not words & _DENIAL_WORDS
            if not guarded or not _negated(normalised, m.start()):
                spans.append(_Cancel(m.start(), m.end(), bool(words & _FORGET_WORDS)))
    return spans


_PAST_REACH_BACK = 4  # words before a past phrase that it still covers ("me cai hace anos")


def _words_back(normalised: str, pos: int, floor: int) -> int:
    """The offset `_PAST_REACH_BACK` words before `pos`, never before `floor`."""
    offset = pos
    for _ in range(_PAST_REACH_BACK):
        previous = normalised.rfind(" ", floor, max(offset - 1, floor))
        offset = floor if previous < 0 else previous + 1
        if offset <= floor:
            return floor
    return offset


def _after_past(past_clauses: dict[int, int], clause: int, pos: int) -> bool:
    """True if `pos` is covered by a past phrase in the same clause: after it, or at
    most a few words before it."""
    return clause in past_clauses and pos >= past_clauses[clause]


def _scan(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """(signals found, signals denied with no fire left) in one transcript.

    A `cancels` match removes only the fires that overlap its own words, so "I didn't
    fall, someone punched me in the mouth" keeps "punched". A `past` match removes
    the signal's fires that come after it in its own clause, or a few words before it
    ("me cai hace diez anos"), unless that clause is about now. So "years ago I broke
    my nose, but now I fell" keeps "now I fell", and "he fell off his bike same thing
    happened two years ago" keeps "fell". The second set holds the
    signals that a denial or an end ("no fever", "ya ha parado de sangrar") took
    away; only those make the tracker forget a remembered part.
    """
    normalised, starts, punctuated = _normalise_with_clauses(text)
    found: set[str] = set()
    denied: set[str] = set()
    fire_ends: dict[str, int] = {}
    bleeding_cancels: list[_Cancel] = []
    for name, (fires, cancels, past) in _COMPILED.items():
        local = _live_cancels(cancels, normalised, punctuated)
        if name == "bleeding":
            bleeding_cancels = local
        # clause index -> from where a past phrase in it covers the clause
        past_clauses: dict[int, int] = {}
        for pattern in past:
            for m in pattern.finditer(normalised):
                if any(m.start() < p < m.end() for p in punctuated):
                    continue  # like a cancel, a past phrase never runs across punctuation
                index = _clause(starts, m.end() - 1)
                if not _PRESENT.search(_clause_text(normalised, starts, index)):
                    reach = _words_back(normalised, m.start(), starts[index])
                    past_clauses[index] = min(reach, past_clauses.get(index, reach))
        ends = [
            b
            for pattern in fires
            for a, b in (m.span() for m in pattern.finditer(normalised))
            if not any(a < c.end and c.start < b for c in local)
            and not _after_past(past_clauses, _clause(starts, a), a)
        ]
        if ends:
            found.add(name)
            fire_ends[name] = max(ends)
        elif any(c.forgets for c in local):
            denied.add(name)
    # "It stopped bleeding, but now it's pouring and won't stop" or "it wasn't bleeding
    # this morning but now it won't stop": a denial or an end said before a fresh
    # "won't stop" does not take the bleeding away. One said after it ("the pain is
    # constant, no bleeding") still does.
    if (
        "heavy" in found
        and "bleeding" in denied
        and fire_ends["heavy"] > min(c.start for c in bleeding_cancels if c.forgets)
    ):
        found.add("bleeding")
        denied.discard("bleeding")
    return frozenset(found), frozenset(denied)


def signals(text: str) -> frozenset[str]:
    """The signals one transcript carries, before they are combined into reasons."""
    return _scan(text)[0]


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
    if "bleeding" in found and ("heavy" in found or "duration" in found):
        reasons.append("red_flag_bleeding")
    return reasons


# Parts remembered across turns. A duration ("since yesterday", "all day") is not:
# it only counts next to a mention of bleeding in the same turn.
_REMEMBERED = frozenset({"swelling", "fever", "bleeding", "heavy", "blow", "face", "injury"})
# A blow is never forgotten: a caller who denies one knock ("he didn't hit his head")
# may still have had another.
_NEVER_FORGOTTEN = frozenset({"blow"})
_PARTS = {
    "red_flag_breathing": frozenset[str](),
    "red_flag_swelling_fever": frozenset({"swelling", "fever"}),
    "red_flag_trauma": frozenset({"blow", "face", "injury"}),
    "red_flag_bleeding": frozenset({"bleeding", "heavy"}),
}


class RedFlagTracker:
    """One per call: the red flags of each transcript, with memory across turns.

    The parts of a combined red flag are remembered, in either order, because a
    caller often gives one part in one turn and the rest in the next: "tengo la cara
    hinchada" then "y ahora tengo fiebre"; "my gums started bleeding earlier" then
    "still hasn't stopped"; "my son fell off the swing" then "his front tooth is
    loose now", or the tooth first and the fall second. A remembered part only
    completes a red flag in a turn that brings a part of its own, so an ordinary later
    turn ("si, gracias") never fires again. A later denial or end of a part ("no
    tengo fiebre", "ya ha parado de sangrar") makes the tracker forget it; a blow is
    never forgotten.

    Pure: it reads no clock and no file. The call plumbing feeds it every final
    caller transcript, echo included, and enforces the playback limits of §6.2.
    """

    def __init__(self) -> None:
        self.memory: set[str] = set()

    def feed(self, text: str) -> list[str]:
        """The red flags of this transcript, read with what earlier turns showed."""
        current, denied = _scan(text)
        forget = (denied - _NEVER_FORGOTTEN) | ({"heavy"} if "bleeding" in denied else set())
        earlier = self.memory - forget
        self.memory = earlier | (current & _REMEMBERED)
        both = current | earlier
        reasons = set(_reasons(current))
        if current & {"swelling", "fever"} and {"swelling", "fever"} <= both:
            reasons.add("red_flag_swelling_fever")
        if current & {"bleeding", "heavy"} and {"bleeding", "heavy"} <= both:
            reasons.add("red_flag_bleeding")
        if current & {"blow", "face", "injury"} and "blow" in both and both & {"face", "injury"}:
            reasons.add("red_flag_trauma")
        # A red flag that fired uses up its remembered parts, so a later turn about the
        # same tooth ("can you see me today for the tooth?") does not fire it again.
        for reason in reasons:
            self.memory -= _PARTS[reason]
        return [reason for reason in REASONS if reason in reasons]
