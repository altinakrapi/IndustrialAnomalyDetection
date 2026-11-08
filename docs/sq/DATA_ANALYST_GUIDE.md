# Udhezuesi i Analistit te te Dhenave

## Importo dhe eksploro te dhenat

Hap **Datasets** per te importuar te dhenat dhe **Exploration** per te kontrolluar statistikat.

## Ekzekuto nje analize

Hap **Analysis**, zgjidh konfigurimin dhe te dhenat, pastaj ekzekuto analizen. Rezultati mbetet evidence statistikore, jo diagnoze fizike.

Historiku i analizave i ndan rezultatet e pajisjeve te regjistruara nga dataset-et NASA IMS dhe i permbledh analizat automatike per cdo matje ne nje rresht 24-oresh; hape per te pare listen me faqe. Analizat e pajisjeve perdorin parametrin e zgjedhur dhe llojin e regjistruar te burimit. Krahasimet dhe vleresimet e simulatorit ruhen vecmas nga historiku i analizave. Administratoret dhe Analistet e te Dhenave mund te nisin analiza dhe te krahasojne rezultate; Tekniket e Mirembajtjes kane vetem lexim.

## Vlereso modelet

Hap **Evaluation** per te shqyrtuar vecmas vleresimet e simulatorit dhe validimin NASA IMS. Precision, Recall, F1, norma e alarmeve te rreme dhe vonesa e zbulimit per simulatorin llogariten vetem nga ekzekutimet e etiketuara qe jane ruajtur; rezultatet per zhvillim sherbejne per eksplorimin e parametrave, ndersa rezultatet per vleresim jane kontrolli i vecuar. NASA IMS jep evidence pershkruese per kushinetat dhe kohen, por nuk ka etiketa defekti per cdo dritare per llogaritjen e metrikave te saktesise. Trajtoji rezultatet dhe alarmet si evidence statistikore per shqyrtim njerezor, jo si diagnoze te defektit fizik. Administratoret dhe Analistet e te Dhenave mund te krijojne vleresime simulatori dhe te rregullojne konfigurimet; Tekniket e Mirembajtjes mund t'i shqyrtojne rezultatet vetem per lexim.

## Krahaso dhe tuning

Perdor kontrollet ne **Analysis** dhe **Evaluation**. Krijimi dhe leximi i tuning-ut jane te lejuara per Administratorin dhe Analistin te te Dhenave.

## Krijo mirembajtje nga nje anomali

Në **Anomalies**, kontrollo burimin dhe evidencen dhe krijo porosinë. Analiza e një anomalije nuk është diagnozë fizike.

Faqja **Anomalies** i grupon rezultatet operative sipas pajisjes, parametrit dhe intervaleve kohore kalendarike; parazgjedhja është 10 minuta dhe ka mundësi 1, 5, 10, 15, 30 dhe 60 minuta, ndërsa çdo rresht tregon intervalin kohor sipas zonës së shfletuesit. Çdo interval përmbledh numrin e anomalive, diapazonin e vlerave dhe njësinë, ashpërsinë më të lartë, detektorët, statusin e shqyrtimit dhe numrin e të pashqyrtuarave. Hap intervalin për të ngarkuar anomalitë individuale (deri në 50 për faqe) dhe përdor **Open details** për evidencën dhe feedback-un. Faqet e intervaleve përmbajnë deri në 50 intervale. Pamja e listës së sheshtë, filtrat për pajisje, parametër, periudhë, burim, status, ashpërsi, detektor dhe kërkim, si dhe eksporti CSV, mbeten të disponueshme. Numri i pajisjes llogarit rreshtat e anomalive; numri i veçantë i dritareve të matjes heq dublikatat për të njëjtën matje.
Te **Monitoring**, shqyrto gjendjen e pajisjeve me burimin e shenuar, numrin e anomalive sipas matjeve unike, grupet e fundit sipas pajisjes dhe parametrit, si dhe historikun e analizave te deshtuara ose te planifikuara. Vetem Administratori mund te menaxhoje planifikimin e dataset-eve NASA IMS te **Settings**; Monitoring nuk ka kontroll per nisje manuale te ciklit. Matjet e pajisjeve analizohen kur mberrijne dhe kontrollet per ndalimin e raportimit kryhen pavaresisht planifikimit. Te dhenat NASA IMS jane eksperimente historike, ndersa matjet e simuluara jane te dhena sintetike testuese, jo telemetri fizike.
