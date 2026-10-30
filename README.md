# Forged Carbon Mold

Lokale CAD-Anwendung für den STEP-Import und die Konstruktion einer ersten
zweiteiligen Forged-Carbon-Pressform. Exakte Volumenkörper und STEP-Dateien
werden mit CadQuery/Open CASCADE verarbeitet; die Vorschau ist eine
tessellierte Darstellung dieser CAD-Geometrie. Die Anwendung läuft lokal und
benötigt für CAD-Berechnungen keinen Cloud- oder Sprachmodelldienst.

## Voraussetzungen

- Windows 10/11 x64
- Python 3.11 oder 3.12
- Internetzugang bei der erstmaligen Installation der Python-Abhängigkeiten
- Für CadQuery muss ausreichend Arbeitsspeicher für das importierte CAD-Modell
  verfügbar sein.

## Installation und Start

In PowerShell im Projektordner:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m forged_carbon_mold
```

Die Anwendung bindet standardmäßig ausschließlich an die Loopback-Adresse.
Für den Start nach der Installation `START.bat` doppelklicken. Das Skript
startet den Server in einem eigenen sichtbaren Konsolenfenster, wartet auf
seine Bereitschaft und öffnet anschließend den Browser. Das Serverfenster
muss geöffnet bleiben; wird es geschlossen, stoppt auch die Anwendung.
Alternativ lässt sich `http://127.0.0.1:8765` nach dem manuellen Start im
Browser öffnen.

## Aktueller Umfang

- STEP/STP-Import, Prüfung auf gültige geschlossene Volumenkörper und Anzeige
  von Körperzahl, Abmessungen und Volumen.
- Lokaler Referenzquader zum Ausprobieren ohne eigene CAD-Datei.
- Bauteilrotation X → Y → Z um den Ursprung und anschließende Translation;
  die Werkzeugachsen werden dabei nicht transformiert.
- Quaderförmiger Rohling mit einstellbarer Wandstärke und ebener Teilung in
  Unter- und Oberform.
- GPU-beschleunigte interaktive WebGL-Vorschau der einmal geladenen
  CAD-Netze; Drehen und Zoomen aktualisieren nur die Ansicht, nicht erneut
  die Geometrie. Die Ansicht wird mit invertierter Maussteuerung gedreht.
  Bauteil, Form oder beide zusammen lassen sich umschalten; Transparenz und
  eine Konturansicht mit Außen- und scharfen Formkanten statt interner
  Dreiecksverbindungen sind zuschaltbar. Eine mit der Ansicht mitdrehende
  XYZ-Markierung zeigt die Achsenorientierung. Echter
  STEP-Export der beiden Segmente und der Baugruppe. Export prüft nach erneutem
  Einlesen Körperzahl, Volumen und Lage/Abmessungen.
- Projektdatei `.fcm` mit den ursprünglichen STEP-Daten und den Parametern;
  beim Öffnen werden CAD-Körper erneut aus der Quelldatei rekonstruiert.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Die Tests erzeugen CAD-Prüfkörper, berechnen eine zweiteilige Form, prüfen
Volumen und Gültigkeit, importieren STEP-Exporte erneut und prüfen den
lokalen API-/Projekt-Rundlauf.

## Grenzen und nächste Schritte

Der belegte Umfang entspricht dem CAD-Machbarkeitstest M0 und einem lokalen
vertikalen Durchlauf M1. Noch nicht implementiert sind unter anderem
Mehrfachsegmentierung, Auswahl einzelner STEP-Körper oder topologischer
Flächen/Kanten, 3D-Trennkonturen, Verbindungen, U-Aussparungen, Keilfasen,
Entformungsprüfung, Undo/Redo und langlebige Projektverwaltung. Die aktuelle
ebene Trennung ist keine Ersatzimplementierung für räumliche Trennkonturen.
Es gibt keine automatische Festigkeits-, Temperatur-, Entformungs- oder
Fertigungsfreigabe. Die Vorschau ist nur eine Darstellung; maßgeblich sind
die prüfbaren STEP-B-Rep-Geometrien.

STEP-Dateien werden nicht an externe Dienste übertragen. Die Quelldatei wird
in einer gespeicherten `.fcm`-Projektdatei eingebettet.
