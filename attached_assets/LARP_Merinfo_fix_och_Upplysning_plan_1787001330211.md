# LARP v0.3.1 — Merinfo Geographic Accuracy Fix + Upplysning.se-plan

## LARP v0.3.1 — Merinfo Geographic Accuracy Fix

Du arbetar vidare i det nuvarande LARP-repot.

Läs först den aktuella implementationen innan du ändrar något.

V0.3 Search Engine finns redan och ska behållas:

- `scanned_count`
- `qualified_count`
- exact target
- hard scan budget
- age-filter
- phone-filter
- housing-filter
- balanced / target / exhaust
- SSE-progress
- progress per område
- deduplicering
- befintlig Merinfo-provider

Denna uppgift gäller **endast förbättrad geografisk precision för Merinfo**.

Gör ingen större arkitekturrefaktor och implementera inte Supabase, Lists, AI, Companies eller Upplysning i denna uppgift.

---

# PROBLEMET

En Merinfo-sökning på exempelvis:

```text
168 50
```

tolkas inte tillräckligt strikt som:

```text
postnummer = 168 50
```

Sökningen kan istället returnera träffar som exempelvis:

```text
Infanterigatan 168, 723 50 Västerås
Hällestadsvägen 168, 247 50 Dalby
Flohemsvägen 168, 254 50 Helsingborg
Täbyvägen 168, 187 50 Täby
Ståltrådsvägen 50, 168 68 Bromma
```

Trots att användaren egentligen ville ha:

```text
168 50 Bromma
```

Detta gör att fel personer kan:

- scannas
- kvalificeras
- räknas mot target
- exporteras

Det ska inte längre vara möjligt.

---

# MÅL

Varje sökområde ska bestå av:

```text
postal_code
+
city
```

Exempel:

```json
{
  "postal_code": "16850",
  "city": "Bromma"
}
```

LARP ska:

1. använda både postnummer och postort när Merinfo-sökningen skapas
2. extrahera det faktiska postnumret från varje resultat
3. extrahera den faktiska postorten från varje resultat
4. validera resultatet mot det efterfrågade området
5. rejecta geografiskt felaktiga resultat
6. endast geografiskt korrekta personer får räknas som `qualified`

---

# DEL 1 — ÄNDRA AREA-MODELLEN

Nuvarande område/postnummer ska utökas så att ett område kan representeras som:

```python
{
    "postal_code": "16850",
    "city": "Bromma"
}
```

Undvik att låta `"16850 Bromma"` bli den permanenta datamodellen.

Postnummer och ort ska vara separata fält internt.

Behåll backward compatibility där det är rimligt.

---

# DEL 2 — UI

För varje valt område ska användaren kunna ange:

```text
Postnummer       Postort
[16850]          [Bromma]
```

Exempel med flera områden:

```text
16850    Bromma
16851    Bromma
16764    Bromma
16858    Bromma
```

Behåll nuvarande funktion för flera områden.

Postnummer ska normaliseras så att:

```text
16850
168 50
```

representerar samma postnummer internt:

```text
16850
```

Postort ska trimmas och jämföras case-insensitive.

---

# DEL 3 — MERINFO QUERY

Merinfo-provider ska använda både postnummer och postort för att göra sökningen mer specifik.

Exempel:

```text
168 50 Bromma
```

istället för endast:

```text
168 50
```

Detta är endast första filtret.

LARP får fortfarande INTE anta att alla resultat från Merinfo är geografiskt korrekta.

Eftervalidering är obligatorisk.

---

# DEL 4 — EXTRAHERA POSTNUMMER FRÅN RESULTAT

Skapa en central funktion, exempelvis:

```python
_extract_postal_code(address)
```

Den ska kunna extrahera svenska postnummer från adresssträngar.

Exempel:

```text
Zornvägen 38, 168 50 Bromma
```

→

```text
16850
```

Exempel:

```text
Infanterigatan 168, 723 50 Västerås
```

→

```text
72350
```

Telefonnummer, gatunummer och andra siffror får inte förväxlas med postnummer.

Normalisera postnummer utan mellanslag.

---

# DEL 5 — EXTRAHERA POSTORT

Använd befintlig city-extraction där den fungerar, men säkerställ att den kan hämta:

```text
Bromma
Västerås
Dalby
Helsingborg
Täby
```

från den faktiska adressen.

Resultatet ska exempelvis normaliseras till:

```python
actual_city = "Bromma"
```

Den sökta postorten får aldrig automatiskt skrivas in som personens verifierade city.

---

# DEL 6 — GEOGRAPHIC VALIDATION

Skapa central logik, exempelvis:

```python
_validate_geography(person, area)
```

För:

```json
{
  "postal_code": "16850",
  "city": "Bromma"
}
```

ska personen endast passera om:

```text
actual_postal_code == 16850
AND
actual_city == Bromma
```

## EXEMPEL — GODKÄND

```text
Requested:
16850 Bromma

Result:
Zornvägen 38, 168 50 Bromma
```

→ ACCEPT

## EXEMPEL — FEL POSTNUMMER

```text
Requested:
16850 Bromma

Result:
Ståltrådsvägen 50, 168 68 Bromma
```

→ REJECT

Reason:

```text
wrong_postal_code
```

## EXEMPEL — FEL POSTORT

```text
Requested:
16850 Bromma

Result:
Täbyvägen 168, 187 50 Täby
```

→ REJECT

Reason:

```text
wrong_location
```

## EXEMPEL — HELT FEL OMRÅDE

```text
Requested:
16850 Bromma

Result:
Infanterigatan 168, 723 50 Västerås
```

→ REJECT

---

# DEL 7 — GEOGRAFI SKA VALIDERAS FÖRE QUALIFIED

Pipeline ska vara:

```text
raw person
↓
normalize
↓
deduplicate
↓
extract actual postal code
↓
extract actual city
↓
validate geography
↓
phone filter
↓
housing filter
↓
age filter
↓
QUALIFIED
```

En geografiskt felaktig person får ALDRIG öka:

```text
qualified_count
```

---

# DEL 8 — TARGET SKA FORTSÄTTA EFTER WRONG LOCATION

Exempel:

```text
Target:
500

Area:
16850 Bromma

Phone:
Required
```

LARP kanske gör:

```text
Scanned             900
Wrong location      280
No phone            115
Duplicates            5
Qualified           500
```

Det är korrekt.

`wrong_location` är bara en rejected person.

Systemet ska fortsätta tills:

```text
qualified_count == target_count
```

eller riktig stop condition nås.

---

# DEL 9 — NYA COUNTERS

Lägg till minst:

```text
wrong_location_count
```

Gärna separat internt:

```text
wrong_postal_code_count
wrong_city_count
```

men UI kan slå ihop dem till:

```text
Fel område
```

om det håller gränssnittet renare.

---

# DEL 10 — GLOBAL PROGRESS

Exempel:

```text
Mål                      500
Godkända                 381
Kontrollerade            724

Fel område               191
Saknar telefon           113
Dubbletter                11
Övriga filter             28
```

Behåll befintliga v0.3-räknare.

---

# DEL 11 — PER-AREA PROGRESS

Exempel:

```text
16850 Bromma

Scanned                 211
Qualified               103
Wrong location           62
No phone                 39
Duplicates                7
```

SSE ska inkludera geografisk rejection-statistik.

---

# DEL 12 — RESULTATMODELL

Varje kvalificerad person ska ha riktiga geografiska fält:

```json
{
  "address": "Zornvägen 38",
  "postal_code": "16850",
  "city": "Bromma"
}
```

Undvik att endast ha en stor ostrukturerad adress om informationen redan kan extraheras på ett säkert sätt.

Behåll gärna originaladressen också:

```text
address_raw
```

om det hjälper debugging.

---

# DEL 13 — LOGGNING

När ett resultat rejectas geografiskt:

```text
[MERINFO]
requested=16850/Bromma
actual=72350/Västerås
result=wrong_location
```

Logga inte mer persondata än vad som behövs för felsökningen.

---

# DEL 14 — TESTER

Lägg till riktiga tester för geografivalideringen.

## TEST 1

Requested:

```text
16850 Bromma
```

Input:

```text
Zornvägen 38, 168 50 Bromma
```

Expected:

```text
valid = true
```

## TEST 2

Requested:

```text
16850 Bromma
```

Input:

```text
Infanterigatan 168, 723 50 Västerås
```

Expected:

```text
valid = false
reason = wrong_location
```

## TEST 3

Requested:

```text
16850 Bromma
```

Input:

```text
Ståltrådsvägen 50, 168 68 Bromma
```

Expected:

```text
valid = false
reason = wrong_postal_code
```

## TEST 4

Verifiera normalisering:

```text
16850
168 50
```

→ samma postnummer.

## TEST 5

Case-insensitive city:

```text
Bromma
BROMMA
bromma
```

→ samma ort.

## TEST 6 — TARGET

Mocka:

```text
Target = 5
```

med:

```text
10 scanned
3 wrong location
2 without phone
5 valid with phone
```

Expected:

```text
qualified = 5
```

## TEST 7 — WRONG LOCATION DOES NOT COUNT

En geografiskt felaktig person med giltigt telefonnummer får inte räknas som qualified.

---

# DEL 15 — BACKWARD COMPATIBILITY

Följande ska fortsätta fungera:

- v0.3 exact target
- balanced
- target
- exhaust
- age filter
- housing filter
- phone filter
- SSE
- schedules
- history
- CSV
- Merinfo
- befintlig dedupe

---

# INGEN SCOPE CREEP

Implementera INTE i denna uppgift:

```text
Upplysning
Supabase
Lists
Customers
Companies
AI
Maps
Lantmäteriet
nya providers
```

---

# DEFINITION OF DONE

Merinfo-fixen är färdig när:

```text
✓ Varje area har postal_code + city
✓ Merinfo-sökningen använder båda
✓ Faktiskt postnummer extraheras ur resultatet
✓ Faktisk postort extraheras ur resultatet
✓ Båda eftervalideras
✓ Fel område räknas aldrig som qualified
✓ wrong_location_count finns
✓ Qualified target fortsätter trots geografiska rejects
✓ SSE visar geografiska rejects
✓ Tester täcker de verkliga Merinfo-felen
```

Det viktigaste är:

> **Merinfos sökresultat ska betraktas som kandidater, inte som sanningen. Den faktiska adressen avgör om personen hör till det efterfrågade området.**

---

# Upplysning.se — sammanfattning av vad som behövs

För Upplysning är målet i grunden samma som med Merinfo: att få ytterligare en källa som kan bidra med personer och framför allt telefonnummer, men med bättre möjlighet till detaljerad geografisk filtrering.

Det som kommer behövas är ungefär:

- en separat **Upplysning-provider** i LARP
- stöd för deras **Detaljerad sökning**
- postnummer + postort som geografiska filter
- stöd för relevanta ytterligare filter, exempelvis ålder och kön där de är användbara
- samma normaliserade personformat som Merinfo
- samma exakta eftervalidering av postnummer + postort
- telefonnummer ska kunna bidra till LARPs globala `qualified_count`
- cross-source deduplicering så samma person från Merinfo och Upplysning bara räknas en gång
- statistik över vilken källa som gav personen/numret
- provider fallback så LARP kan fortsätta till nästa källa om första källan inte räcker
- ett sessions-/behörighetslager för den åtkomst du legitimt har till ditt Upplysning-konto, utan att bygga funktioner för att kringgå CAPTCHA eller andra tekniska åtkomstspärrar
- Search Planner så LARP vet vilka geografiska/filtermöjligheter respektive provider stödjer
- senare lagring i Supabase så en person kan ha flera `sources` och flera verifieringsdatum

## Målbild

```text
POSTNUMMER + POSTORT
        ↓
   PROVIDER ENGINE
      ↙       ↘
 Merinfo    Upplysning
      ↘       ↙
      NORMALIZER
          ↓
 GEOGRAPHIC VALIDATION
          ↓
    CROSS-SOURCE DEDUPE
          ↓
 PHONE / AGE / HOUSING
          ↓
      QUALIFIED
          ↓
        TARGET
```

Nästa steg efter Merinfo-fixen är därför:

**v0.3.2 — Multi-source Provider Engine**, där Upplysning blir provider nummer två.
