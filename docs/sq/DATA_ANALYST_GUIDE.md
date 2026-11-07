# Udhezuesi i Analistit te te Dhenave

## Importo dhe eksploro te dhenat

Hap **Datasets** per te importuar te dhenat dhe **Exploration** per te kontrolluar statistikat.

## Ekzekuto nje analize

Hap **Analysis**, zgjidh konfigurimin dhe te dhenat, pastaj ekzekuto analizen. Rezultati mbetet evidence statistikore, jo diagnoze fizike.

## Vlereso modelet

Hap **Evaluation** dhe krijo vleresime simulatori. NASA IMS nuk ka etiketa per secilen dritare dhe nuk jep metrika te saktesise per dritare.

## Krahaso dhe tuning

Perdor kontrollet ne **Analysis** dhe **Evaluation**. Krijimi dhe leximi i tuning-ut jane te lejuara per Administratorin dhe Analistin te te Dhenave.

## Krijo mirembajtje nga nje anomali

Në **Anomalies**, kontrollo burimin dhe evidencen dhe krijo porosinë. Analiza e një anomalije nuk është diagnozë fizike.

Faqja **Anomalies** i grupon rezultatet operative të pajisjeve të regjistruara fillimisht sipas pajisjes dhe pastaj sipas parametrit. Zgjero një pajisje për të shqyrtuar matjet më të reja; çdo faqe e pajisjes përmban deri në 50 matje. Përdor butonin e listës së sheshtë për të ndryshuar paraqitjen. Filtrat për pajisjen, periudhën, burimin dhe statusin, si dhe eksporti CSV, janë të disponueshëm. Numri i anomalive llogarit rreshtat e anomalive, ndërsa numri i veçantë i dritareve të matjes bashkon rreshtat që i përkasin së njëjtës matje. Kjo nuk është pemë episodesh dhe nuk ka grup të veçantë për anomalitë e palidhura me episode. Pamja operative përjashton pajisjet e arkivuara, analizat sipas kërkesës/historike/vlerësuese/tuning/krahasuese/validuese dhe anomalitë e datasetit NASA IMS; këto të fundit mbeten te analiza e datasetit. Feedback-u dhe porositë e mirëmbajtjes kryhen nga një anomali individuale, jo nga një episod. Ekziston një endpoint API për feedback mbi episodin, por nuk është rrjedhë pune në ndërfaqen e pemës së episodeve.
