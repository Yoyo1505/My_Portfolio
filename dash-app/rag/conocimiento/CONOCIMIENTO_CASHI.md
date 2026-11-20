# Conocimiento de Cashi: estructura, gastos y tipos de consulta

Guia para Cashi sobre la estructura del tablero de gasto de la red fisica de Aurora Retail Group, que dicen los datos y como preguntar. Datos sinteticos para demostracion; semana real cerrada: 42.

## Series y semanas

- **R26** = Real 2026, **P26** = Plan 2026, **R25** = Real 2025 (anio anterior).
- **Sobregasto / variacion vs Plan** = Real 2026 menos Plan 2026. Positivo = exceso (desfavorable), negativo = ahorro.
- **Variacion vs anio anterior** = Real 2026 menos Real 2025, a las mismas semanas.
- El anio fiscal tiene 53 semanas; los trimestres son Q1 (1-13), Q2 (14-26), Q3 (27-39) y Q4 (40-53).
- El Real llega hasta la semana de corte; despues solo existe el Plan y el Forecast.

## Estructura territorial

Agrupa 1 (Canales Fisicos) > Division > Territorio > Zona > Region > PDC (punto de venta) > CECO.

- **Division Norte**: Baja California, Bajio, Chihuahua, Jalisco.
- **Division Centro**: Metro Norte, Metro Sur, Queretaro.
- **Division Sur**: Chiapas, Oaxaca, Veracruz, Yucatan.
- **Corporativo**: Territorio Corporativo.

## Estructura de cuentas

Grupo de Cuentas > Cuentas. Grupos: Nomina, Renta Luz y Telefono, Mantenimiento, Logistica, Mercadotecnia, Tecnologia y Seguridad.

## Tipos de consulta

- **Comparar**: "Real 2026 vs Real 2025", "Real 2026 vs Plan 2026", "variacion de la semana 30 vs 31".
- **Rankings**: "Top 10 CECOs con mayor sobregasto", "Pareto de gasto", "top variaciones vs Plan".
- **Alertas**: "senales de sobregasto", "que semanas fueron atipicas".
- **Cuentas**: "gasto de nomina por division", "gasto de logistica por semana".
- **Territorio**: "gasto de la division norte", "gasto del territorio Bajio".

## Reglas de lectura

- Las cifras se muestran en millones de pesos (MDP) en Monto Constante; tambien existe Monto Local.
- Un CECO atipico es el que se sale de su historia propia con un z-score robusto y una desviacion material; un modelo no supervisado ofrece una segunda opinion.
