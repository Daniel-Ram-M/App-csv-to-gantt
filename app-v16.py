import itertools
import json
import re

import pandas as pd
import streamlit as st
from packaging.version import Version

st.set_page_config(
    page_title="Gestor Avanzado CSV", page_icon="⚡", layout="wide"
)

ANCHO = (
    {"width": "stretch"}
    if Version(st.__version__) >= Version("1.50.0")
    else {"use_container_width": True}
)

NINGUNA = "(ninguna)"
VISTA_TABLA = "📋 Tabla, edición e inspector"
VISTA_GANTT = "📈 Diagrama de Gantt"

st.title("⚡ Gestor estilo Notion")
st.markdown("Cambia entre la vista de tabla/edición y el diagrama de Gantt.")

# ====================== FUNCIONES AUXILIARES ======================
def detectar(columnas, patrones, excluir=(), por_patron=False):
    def coincide(c, p):
        for t in re.split(r"[^a-z0-9áéíóúñü]+", c.lower()):
            if (p.endswith("*") and t.startswith(p[:-1])) or t == p:
                return True
        return False

    if por_patron:
        for p in patrones:
            for c in columnas:
                if c not in excluir and coincide(c, p):
                    return c
        return None
    for c in columnas:
        if c not in excluir and any(coincide(c, p) for p in patrones):
            return c
    return None


def limpiar_fecha(serie, lado=None):
    s = serie.astype(str)
    if lado == "ini":
        s = s.str.split("→").str[0]
    elif lado == "fin":
        s = s.str.split("→").str[-1]
    s = s.str.replace(r"(\d+)(st|nd|rd|th)", r"\1", regex=True)
    s = s.str.replace(r"\s*(Z|[+-]\d{2}:?\d{2})\s*$", "", regex=True)
    return pd.to_datetime(s, errors="coerce", format="mixed")


def extraer_valores(celda):
    if pd.isna(celda):
        return []
    s = str(celda)
    for ch in "[]'\"":
        s = s.replace(ch, "")
    return [p.strip() for p in s.split(",") if p.strip()]


def opciones_columna(serie):
    opciones = {}
    for celda in serie.dropna():
        for v in extraer_valores(celda):
            opciones.setdefault(v.lower(), v)
    return sorted(opciones.values(), key=str.lower)


def filtrar_multi(df, col, elegidas):
    elegidas_l = {e.lower() for e in elegidas}
    mask = df[col].apply(
        lambda c: any(v.lower() in elegidas_l for v in extraer_valores(c))
    )
    return df[mask]


def a_texto(v):
    return str(v).strip() if pd.notna(v) else ""


MODO_SUB = "La celda lista las SUBTAREAS de esta tarea"
MODO_MADRE = "La celda indica la tarea MADRE de esta tarea"
MODO_DEP = "La celda lista tareas de las que DEPENDE (bloqueada por)"
MODO_REL = "La celda lista tareas ENLAZADAS (o que esta bloquea)"
MODOS = [MODO_SUB, MODO_MADRE, MODO_DEP, MODO_REL]


def norm_ref(t):
    t = re.sub(r"\s*\(https?://[^)]*\)\s*$", "", str(t)).strip().lstrip("#").strip().lower()
    return t[:-2] if re.fullmatch(r"\d+\.0", t) else t


def referencias(celda):
    if pd.isna(celda):
        return []
    toks = [norm_ref(v) for v in extraer_valores(celda)]
    todo = norm_ref(celda)
    if todo and todo not in toks:
        toks.append(todo)
    return [t for t in toks if t and t not in ("nan", "none", "null", "-")]


def indices_tareas(df, col_id, col_nombre):
    por_id, por_nombre = {}, {}
    ids = df[col_id].tolist() if col_id else [None] * len(df)
    for idx, i, n in zip(df.index.tolist(), ids, df[col_nombre].tolist()):
        if i is not None and pd.notna(i) and a_texto(i):
            por_id.setdefault(norm_ref(a_texto(i)), idx)
        if pd.notna(n) and a_texto(n):
            por_nombre.setdefault(norm_ref(a_texto(n)), []).append(idx)
    return por_id, por_nombre


def resolver(tok, por_id, por_nombre):
    if tok in por_id:
        return por_id[tok]
    c = por_nombre.get(tok)
    return c[0] if c and len(c) == 1 else None


def construir_enlaces(df, config, col_id, col_nombre, enlaces_manuales=None, enlaces_eliminados=None):
    por_id, por_nombre = indices_tareas(df, col_id, col_nombre)
    aristas = set()
    for c, modo in config.items():
        for idx, cel in zip(df.index.tolist(), df[c].tolist()):
            for t in {resolver(x, por_id, por_nombre) for x in referencias(cel)} - {None, idx}:
                if modo == MODO_SUB:
                    aristas.add((idx, t, 0, "#e2e8f0"))
                elif modo == MODO_MADRE:
                    aristas.add((t, idx, 0, "#e2e8f0"))
                elif modo == MODO_DEP:
                    aristas.add((t, idx, 1, "#5eead4"))
                else:
                    aristas.add((idx, t, 1, "#5eead4"))

    if enlaces_manuales:
        for orig, dest, modo, color in enlaces_manuales:
            aristas.add((orig, dest, 1, color))

    if enlaces_eliminados:
        aristas = {a for a in aristas if (a[0], a[1], a[2]) not in enlaces_eliminados}

    return aristas


# ====================== CARGA DEL CSV ======================
uploaded_file = st.file_uploader("Sube tu archivo CSV para comenzar", type=["csv"])

if uploaded_file is None:
    st.info("👈 Sube un archivo CSV en la parte superior para comenzar.")
    st.stop()

file_key = f"{uploaded_file.name}-{uploaded_file.size}"
if st.session_state.get("file_key") != file_key:
    try:
        try:
            raw = pd.read_csv(uploaded_file, sep=None, engine="python")
        except UnicodeDecodeError:
            uploaded_file.seek(0)
            raw = pd.read_csv(
                uploaded_file, sep=None, engine="python", encoding="latin-1"
            )
    except Exception as e:
        st.error(f"Error al leer el CSV: {e}")
        st.stop()
    st.session_state.raw_df = raw
    st.session_state.file_key = file_key
    st.session_state.enlaces_manuales = []
    st.session_state.enlaces_eliminados = set()

df = st.session_state.raw_df
columnas = list(df.columns)
st.success(f"CSV cargado: {len(df)} filas, {len(columnas)} columnas.")

# ====================== DETECCIÓN DE COLUMNAS ======================
det_tarea = next(
    (c for c in columnas if c.strip().lower() in ("task name", "nombre de la tarea")),
    None,
) or detectar(columnas, ["tarea", "task", "nombre", "name", "title", "título"], por_patron=True)

det_persona = detectar(columnas, ["assignee", "asignado", "asignada", "persona", "respons*", "owner", "assign*", "asign*", "user", "encargad*"], por_patron=True)
det_folder = detectar(columnas, ["folder", "carpeta"], por_patron=True)
det_dep = detectar(columnas, ["space", "departamento", "department", "depart*", "dept", "depto", "area", "área", "equipo", "team"], excluir=(det_folder,), por_patron=True)
det_list = detectar(columnas, ["lista", "list"], excluir=(det_folder,), por_patron=True)
det_estado = detectar(columnas, ["status", "estado"], por_patron=True)
det_id = next((c for c in columnas if c.strip().lower() in ("task id", "id", "task_id")), None)
det_prio = detectar(columnas, ["prio*", "urgen*"])
det_start = detectar(columnas, ["start", "inicio", "desde", "begin"])
det_end = detectar(columnas, ["due", "end", "limite", "límite", "fin", "final", "deadline", "vencimiento"], excluir=(det_start,)) or detectar(columnas, ["fecha", "date"], excluir=(det_start,))


def selector(contenedor, label, detectada, key, obligatorio=False):
    opciones = list(columnas) if obligatorio else [NINGUNA] + list(columnas)
    idx = opciones.index(detectada) if detectada in opciones else 0
    elegida = contenedor.selectbox(label, opciones, index=idx, key=f"{key}_{file_key}")
    return None if elegida == NINGUNA else elegida


with st.expander("⚙️ Columnas detectadas (corrígelas si no son las correctas)"):
    a = st.columns(3)
    b = st.columns(3)
    c3 = st.columns(3)
    c4 = st.columns(3)
    col_tarea_sel = selector(a[0], "Nombre de la tarea", det_tarea or columnas[0], "c_tarea", True)
    col_persona_sel = selector(a[1], "Persona asignada", det_persona, "c_per")
    col_prio_sel = selector(a[2], "Urgencia / prioridad", det_prio, "c_prio")
    col_dep_sel = selector(b[0], "Departamento (Space)", det_dep, "c_dep")
    col_folder_sel = selector(b[1], "Carpeta (Folder)", det_folder, "c_folder")
    col_list_sel = selector(b[2], "Lista (List)", det_list, "c_list")
    col_estado_sel = selector(c3[0], "Estado (Status)", det_estado, "c_estado")
    col_start_sel = selector(c3[1], "Fecha de inicio", det_start, "c_ini")
    col_end_sel = selector(c3[2], "Fecha de fin", det_end, "c_fin")
    col_id_sel = selector(c4[0], "ID de la tarea", det_id, "c_id")

# ====================== FILTROS GLOBALES ======================
st.markdown("---")
st.subheader("🔎 Filtros")
fa = st.columns(4)
fb = st.columns(4)

busqueda = fa[0].text_input(f"Buscar en '{col_tarea_sel}':", placeholder="Ej. tabla valores...")
df_filtrado = df.copy()
if busqueda.strip():
    df_filtrado = df_filtrado[
        df_filtrado[col_tarea_sel]
        .astype(str)
        .str.contains(busqueda.strip(), case=False, na=False, regex=False)
    ]

for ui, etiqueta, col in [
    (fa[1], "🏢 Departamento", col_dep_sel),
    (fa[2], "📁 Carpeta", col_folder_sel),
    (fa[3], "📋 Lista", col_list_sel),
    (fb[0], "👤 Persona", col_persona_sel),
    (fb[1], "🔥 Urgencia", col_prio_sel),
    (fb[2], "🚦 Estado", col_estado_sel),
]:
    if col:
        elegidas = ui.multiselect(
            etiqueta, opciones_columna(df[col]), key=f"filtro_{etiqueta}_{file_key}"
        )
        if elegidas:
            df_filtrado = filtrar_multi(df_filtrado, col, elegidas)
    else:
        ui.caption(f"{etiqueta}: sin columna asignada")

st.caption(f"Mostrando {len(df_filtrado)} de {len(df)} tareas.")

# ====================== SELECTOR DE VISTA ======================
st.markdown("---")
modo_vista = st.radio("📌 Vista:", [VISTA_TABLA, VISTA_GANTT], horizontal=True)
st.markdown("---")

# ====================== VISTA 1: TABLA ======================
def vista_tabla():
    st.subheader("📋 Tabla de datos y edición")
    st.data_editor(df_filtrado, num_rows="dynamic", key="tabla_edicion", **ANCHO)

    st.markdown("---")
    st.subheader("🔍 Inspector de tareas")
    if df_filtrado.empty:
        st.warning("No hay tareas con los filtros actuales.")
    else:
        tareas = df_filtrado.reset_index(drop=True)
        i = st.selectbox(
            "Selecciona una tarea para ver toda su información:",
            range(len(tareas)),
            format_func=lambda i: str(tareas.loc[i, col_tarea_sel]),
        )
        fila = tareas.loc[i]
        st.markdown(f"### 📋 {fila[col_tarea_sel]}")
        cols_info = st.columns(3)
        resto = [c for c in columnas if c != col_tarea_sel]
        for k, c in enumerate(resto):
            cols_info[k % 3].markdown(f"**{c}**\n\n{a_texto(fila[c]) or '—'}")

# ====================== VISTA 2: GANTT ======================
PALETA = [
    "#7F3C8D", "#11A579", "#3969AC", "#F2B701", "#E73F74", "#80BA5A", "#E68310",
    "#008695", "#CF1C90", "#f97b72", "#4b4b8f", "#A5AA99", "#5D69B1", "#52BCA3",
    "#99C945", "#CC61B0", "#24796C", "#DAA51B", "#2F8AC4", "#764E9F", "#ED645A",
    "#88CCEE", "#CC6677", "#DDCC77", "#117733", "#AA4499", "#44AA99", "#999933",
]
URG_COLOR = {"urgent": "#ef4444", "high": "#f97316", "normal": "#3b82f6", "low": "#94a3b8", "none": "#475569"}
URG_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3, "none": 4}
SIN_VALOR = {
    "Departamento": "(sin departamento)", "Carpeta": "(sin carpeta)", "Lista": "(sin lista)",
    "Estado": "(sin estado)", "Urgencia": "(sin urgencia)", "Persona": "(sin asignar)",
}


def valores_nivel(celda, nivel):
    if nivel == "Persona":
        return extraer_valores(celda) or [SIN_VALOR["Persona"]]
    t = a_texto(celda)
    if t == "" or t.lower() == "nan" or (nivel in ("Urgencia", "Carpeta") and t.lower() in ("none", "hidden")):
        return [SIN_VALOR[nivel]]
    return [t]


def urg_clave(v):
    return "none" if v == SIN_VALOR["Urgencia"] else str(v).lower()


def mapa_colores(nivel, serie):
    vals = sorted({v for c in serie for v in valores_nivel(c, nivel)}, key=str.lower)
    return {
        v: (URG_COLOR.get(urg_clave(v)) if nivel == "Urgencia" else None) or PALETA[i % len(PALETA)]
        for i, v in enumerate(vals)
    }


def iso(t):
    return None if t is None or pd.isna(t) else pd.Timestamp(t).strftime("%Y-%m-%d")


def construir_datos_gantt(niveles, disp, color_nivel, orden_t, ver_sin_fechas, edges):
    todas = pd.concat(
        [limpiar_fecha(df[col_start_sel], "ini"), limpiar_fecha(df[col_end_sel], "fin")]
    ).dropna()
    if todas.empty:
        ini_g, fin_g = pd.Timestamp("2025-01-01"), pd.Timestamp("2026-12-31")
    else:
        ini_g, fin_g = todas.min().normalize(), todas.max().normalize()

    ini_s = limpiar_fecha(df_filtrado[col_start_sel], "ini").dt.normalize()
    fin_s = limpiar_fecha(df_filtrado[col_end_sel], "fin").dt.normalize()
    filas = df_filtrado.to_dict("records")

    nom = {i: a_texto(v) or "(sin nombre)" for i, v in zip(df.index.tolist(), df[col_tarea_sel].tolist())}
    padre, n_sub, n_rel, enl = {}, {}, {}, {}
    for edge in sorted(edges):
        a_, b_, k_ = edge[0], edge[1], edge[2]
        if k_ == 0:
            n_sub[a_] = n_sub.get(a_, 0) + 1
            x, ciclo = a_, False
            while x is not None:
                if x == b_:
                    ciclo = True
                    break
                x = padre.get(x)
            if not ciclo and b_ not in padre:
                padre[b_] = a_
            enl.setdefault(a_, []).append("↳ Subtarea: " + nom[b_])
            enl.setdefault(b_, []).append("↑ Madre: " + nom[a_])
        else:
            n_rel[a_] = n_rel.get(a_, 0) + 1
            n_rel[b_] = n_rel.get(b_, 0) + 1
            enl.setdefault(a_, []).append("→ Enlaza con: " + nom[b_])
            enl.setdefault(b_, []).append("← Enlazada desde: " + nom[a_])

    mapa_color = mapa_colores(color_nivel, df[disp[color_nivel]])
    col_color = disp[color_nivel]

    recs = []
    for rid, f, a, b in zip(df_filtrado.index.tolist(), filas, ini_s, fin_s):
        ra = None if pd.isna(a) else a
        rb = None if pd.isna(b) else b
        if ra is None and rb is None:
            p, a, b = 3, ini_g, fin_g
            nota = "Sin fechas: rayado de la primera a la última tarea"
        elif ra is None:
            p, a = 1, ini_g
            nota = "Sin fecha de inicio: rayado desde la primera tarea"
        elif rb is None:
            p, b = 2, fin_g
            nota = "Sin fecha de fin: rayado hasta la última tarea"
        else:
            p, nota = 0, ""
            if b < a:
                a, b = b, a
                ra, rb = a, b
        if p == 3 and not ver_sin_fechas:
            continue
        reales = [d for d in (ra, rb) if d is not None]
        detalle = []
        for c in columnas:
            v = a_texto(f[c])
            if v in ("", "[]", "nan") or "url" in c.lower():
                continue
            detalle.append([c, v if len(v) <= 400 else v[:400] + "…"])
        tid = a_texto(f[col_id_sel]) if col_id_sel else ""
        lista_txt = valores_nivel(f[col_list_sel], "Lista")[0] if col_list_sel else ""
        
        val_color_celda = valores_nivel(f[col_color], color_nivel)[0]
        color_asignado = mapa_color.get(val_color_celda, "#64748b")

        recs.append({
            "id": tid, "rid": rid, "nombre": a_texto(f[col_tarea_sel]) or "(sin nombre)",
            "a": a, "b": b, "ra": ra, "rb": rb, "p": p, "nota": nota,
            "lo": min(reales) if reales else None, "hi": max(reales) if reales else None,
            "personas": ", ".join(extraer_valores(f[col_persona_sel])) if col_persona_sel else "",
            "ctx": "" if ("Lista" in niveles or lista_txt == SIN_VALOR["Lista"]) else lista_txt,
            "sub": n_sub.get(rid, 0), "nl": n_rel.get(rid, 0), "enl": enl.get(rid, [])[:15], "det": detalle,
            "color": color_asignado,
            "niv": {n: valores_nivel(f[disp[n]], n) for n in niveles},
        })

    raiz = {"h": {}, "t": []}
    for r in recs:
        for ruta in itertools.product(*[r["niv"][n] for n in niveles]):
            nodo = raiz
            for k in ruta:
                nodo = nodo["h"].setdefault(k, {"h": {}, "t": []})
            nodo["t"].append(r)

    def agregar(nodo):
        n = len(nodo["t"])
        los = [r["lo"] for r in nodo["t"] if r["lo"] is not None]
        his = [r["hi"] for r in nodo["t"] if r["hi"] is not None]
        for h in nodo["h"].values():
            agregar(h)
            n += h["n"]
            if h["lo"] is not None:
                los.append(h["lo"])
                his.append(h["hi"])
        nodo["n"], nodo["lo"], nodo["hi"] = n, (min(los) if los else None), (max(his) if his else None)

    agregar(raiz)

    def clave_tarea(r):
        if orden_t == "Nombre":
            return (r["nombre"].lower(),)
        if orden_t == "Fecha de fin":
            return (r["b"], r["a"], r["nombre"].lower())
        return (r["a"], r["b"], r["nombre"].lower())

    def tarea_item(r, d):
        return {
            "t": "t", "d": d, "r": r["rid"], "pk": None,
            "l": r["nombre"], "q": r["personas"], "x": r["ctx"], "s": r["sub"], "nl": r["nl"], "e": r["enl"],
            "a": iso(r["a"]), "b": iso(r["b"]), "ra": iso(r["ra"]), "rb": iso(r["rb"]),
            "p": r["p"], "c": r["color"], "o": r["nota"], "f": r["det"],
        }

    items = []

    def hojas(tareas, d):
        ids = {r["rid"] for r in tareas}
        hijos, top = {}, []
        for r in sorted(tareas, key=clave_tarea):
            if padre.get(r["rid"]) in ids:
                hijos.setdefault(padre[r["rid"]], []).append(r)
            else:
                top.append(r)

        def poner(r, dd, pk=None):
            k = len(items)
            it = tarea_item(r, dd)
            it["pk"] = pk
            items.append(it)
            for h in hijos.get(r["rid"], []):
                poner(h, dd + 1, k)

        for r in top:
            poner(r, d)

    def emitir(nodo, d, i):
        if i < len(niveles):
            nivel = niveles[i]
            orden = (lambda k: (URG_RANK.get(urg_clave(k), 9), k)) if nivel == "Urgencia" else str.casefold
            mapa_nivel_color = mapa_colores(nivel, df[disp[nivel]])
            for k in sorted(nodo["h"], key=orden):
                h = nodo["h"][k]
                color_g = mapa_nivel_color.get(k) if nivel in ("Departamento", "Carpeta") else (mapa_color.get(k) if nivel == color_nivel else None)
                items.append({
                    "t": "g", "d": d, "l": k, "n": h["n"], "a": iso(h["lo"]), "b": iso(h["hi"]),
                    "c": color_g,
                })
                emitir(h, d + 1, i + 1)
        hojas(nodo["t"], d)

    emitir(raiz, 0, 0)

    rids_vis = {r["rid"] for r in recs}
    aristas = [[a_[0], a_[1], a_[2], a_[3] if len(a_) > 3 else "#5eead4"] for a_ in sorted(edges) if a_[0] in rids_vis and a_[1] in rids_vis]
    presentes = {r["color"] for r in recs}
    leyenda = [[v, c] for v, c in mapa_color.items() if c in presentes]
    if color_nivel == "Urgencia":
        leyenda.sort(key=lambda x: URG_RANK.get(urg_clave(x[0]), 9))
    return {
        "min": iso(ini_g - pd.Timedelta(days=7)), "max": iso(fin_g + pd.Timedelta(days=7)),
        "zoom": 4, "items": items, "legend": leyenda,
        "n_tareas": len(recs), "n_parciales": sum(1 for r in recs if r["p"]),
        "edges": aristas, "n_enlaces": len(aristas), "n_relaciones": len(edges),
    }


GANTT_HTML = r"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:#12141a;color:#e2e8f0;font:13px/1.2 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;overflow:hidden}
body.dr{cursor:col-resize;user-select:none}
#app{display:flex;flex-direction:column;height:100vh}
#bar{flex:none;display:flex;gap:8px;align-items:center;padding:6px 10px;background:#0e1015;border-bottom:1px solid #262b36;flex-wrap:wrap}
#bar button{background:#1d212b;color:#e2e8f0;border:1px solid #333a49;border-radius:6px;padding:4px 9px;cursor:pointer;font:inherit}
#bar button:hover{background:#2a3040}
#bar label{display:flex;align-items:center;gap:6px;color:#9aa4b5}
#bar input[type=range]{width:120px}
#legend{display:flex;gap:6px;flex-wrap:wrap;max-height:46px;overflow:auto;margin-left:auto}
.chip{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:10px;background:#1a1d24;border:1px solid #2b3140;font-size:12px;white-space:nowrap}
.chip i{width:10px;height:10px;border-radius:3px;display:inline-block}
#main{flex:1;min-height:0;display:flex}
#left{flex:none;width:380px;display:flex;flex-direction:column;overflow:hidden;background:#12141a}
#lhead{flex:none;height:44px;display:flex;align-items:center;padding:0 10px;font-weight:600;color:#9aa4b5;background:#171a21;border-bottom:1px solid #2b3140;white-space:nowrap;overflow:hidden}
#lbody{flex:none;position:relative;overflow:hidden}
#lrows{position:absolute;left:0;right:0;top:0;will-change:transform;
 background-image:repeating-linear-gradient(to bottom,transparent 0,transparent 26px,rgba(255,255,255,.028) 26px,rgba(255,255,255,.028) 52px)}
.lr{height:26px;display:flex;align-items:center;padding-right:8px;cursor:pointer;position:relative}
.lr.g{font-weight:600;background:rgba(255,255,255,.035)}
.lr.g0{background:rgba(120,140,200,.16);border-left:4px solid #3b82f6}
.lr.g1{background:rgba(120,140,200,.09);border-left:4px solid #10b981}
.lr.linking{background:rgba(16,185,129,0.35) !important; outline: 1px dashed #10b981;}
.bar.linking{outline:2px dashed #10b981; z-index:4;}

.dots-btn{background:none;border:none;color:#9aa4b5;cursor:pointer;font-size:14px;padding:0 4px;margin-right:6px;border-radius:3px;display:inline-flex;align-items:center;justify-content:center}
.dots-btn:hover{background:rgba(255,255,255,0.1);color:#fff}

.lr-content{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1}
.lr .ar{display:inline-block;width:14px;color:#9aa4b5}
.lr .dot{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:6px}
.lr .ct{margin-left:8px;font-weight:400;font-size:11px;color:#9aa4b5;background:#222733;border-radius:8px;padding:1px 7px}
.lr .sep{color:#6b7587}
.lr .pp{color:#8fb4e8}
.lr .pp.none{color:#6b7587;font-style:italic}
.lr .cx{color:#7d8799;font-size:11px;margin-left:6px}
.lr .sb{color:#e0b24f;font-size:11px;margin-left:6px}

#lhl,#lsel,#rhl,#rsel{position:absolute;left:0;right:0;height:26px;display:none;pointer-events:none}
#lhl,#rhl{background:rgba(255,255,255,.07)}
#lsel,#rsel{background:rgba(80,140,255,.22);outline:1px solid rgba(80,140,255,.6)}
#split{flex:none;width:9px;background:#262b36;cursor:col-resize;display:flex;align-items:center;justify-content:center;touch-action:none;position:relative;z-index:6}
#split:hover,body.dr #split{background:#3b6fd8}
#split::before{content:"";width:3px;height:34px;border-left:1px solid #8892a6;border-right:1px solid #8892a6}
#right{flex:1;min-width:0;overflow:auto;position:relative;background:#1a1d24}
#rhead{position:sticky;top:0;z-index:5;height:44px;background:#171a21;border-bottom:1px solid #2b3140}
.mh{position:absolute;top:0;height:22px;line-height:22px;padding-left:6px;border-left:1px solid #2f3646;color:#c7d0e0;font-weight:600;overflow:hidden;white-space:nowrap}
.dh{position:absolute;top:22px;height:22px;line-height:22px;text-align:center;color:#7d8799;font-size:11px;overflow:hidden}
#rbody{position:relative;
 background-image:repeating-linear-gradient(to bottom,transparent 0,transparent 26px,rgba(255,255,255,.028) 26px,rgba(255,255,255,.028) 52px)}
.vl{position:absolute;top:0;bottom:0;width:1px;background:#2a3040;pointer-events:none}
.td{position:absolute;top:0;bottom:0;width:2px;background:#ef4444;z-index:3;pointer-events:none}
.bar{position:absolute;height:16px;border-radius:4px;opacity:.92;z-index:2}
.gb{position:absolute;height:8px;border-radius:3px;opacity:.6;z-index:2}
#lk{position:absolute;left:0;top:0;pointer-events:auto;z-index:3;overflow:visible}
.lk .ln{fill:none;stroke-width:3;opacity:.95;stroke-linejoin:round;cursor:pointer}
.lk .ah,.lk .og{stroke:none;opacity:1;cursor:pointer}
.lk.x .ln{stroke-dasharray:4 3}

.bd{position:absolute;z-index:3;pointer-events:none;font-size:10px;line-height:14px;height:14px;padding:0 5px;border-radius:8px;background:#0e1015;border:1px solid #3b4357;color:#fbbf24}
.lk.hl .ln{stroke:#fbbf24 !important;stroke-width:4;opacity:1}
.lk.hl .ah,.lk.hl .og{fill:#fbbf24 !important;opacity:1}

#ctxMenu{display:none;position:absolute;z-index:9999;background:#1d212b;border:1px solid #333a49;border-radius:6px;box-shadow:0 4px 12px rgba(0,0,0,0.5);padding:4px 0;min-width:180px;font-size:12px}
#ctxMenu .citem{padding:8px 14px;cursor:pointer;color:#e2e8f0;display:flex;align-items:center;gap:6px}
#ctxMenu .citem:hover{background:#2a3040;color:#fff}
.color-picker{display:flex;gap:6px;padding:6px 14px;border-top:1px solid #333a49;margin-top:4px}
.color-opt{width:16px;height:16px;border-radius:50%;cursor:pointer;border:1px solid rgba(255,255,255,0.3)}

#detail{display:none;flex:none;height:200px;overflow:auto;border-top:2px solid #3b6fd8;background:#0e1015;padding:8px 12px}
body.det #detail{display:block}
#detail .dt{display:flex;align-items:center;gap:10px;margin-bottom:6px}
#detail .dt b{font-size:14px}
#detail .dt button{margin-left:auto;background:#1d212b;color:#e2e8f0;border:1px solid #333a49;border-radius:6px;cursor:pointer;padding:2px 8px}
#detail .dn{color:#e0b24f;margin-bottom:6px}
#detail .dg{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:6px 18px}
#detail .dg div{overflow-wrap:anywhere;font-size:12px}
#detail .dg span{display:block;color:#7d8799;font-size:11px}
#empty{padding:30px;color:#9aa4b5}
</style></head>
<body>
<div id="app">
  <div id="bar">
    <label>🔍 Zoom <input type="range" id="zm" min="1" max="40" step="1"><span id="zv"></span></label>
    <button id="btnToday">📍 Hoy</button>
    <button id="btnCol">▾ Contraer todo</button>
    <button id="btnExp">▸ Expandir todo</button>
    <button id="btnPanel">◧ Panel de nombres</button>
    <button id="btnLk" title="Mostrar u ocultar las flechas entre tareas">🔗 Relaciones: sí</button>
    <div id="legend"></div>
  </div>
  <div id="main">
    <div id="left">
      <div id="lhead">Tarea - Persona</div>
      <div id="lbody"><div id="lrows"></div></div>
    </div>
    <div id="split" title="Arrastra para ajustar · doble clic para ocultar/mostrar el panel"></div>
    <div id="right"><div id="rhead"></div><div id="rbody"></div></div>
  </div>
  <div id="detail"></div>
</div>

<div id="ctxMenu"></div>

<script>
let D = __DATA__;
const RH = 26, HEAD = 44, DAY = 864e5;
const MES = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const parse = s => { const p = s.split('-').map(Number); return Date.UTC(p[0], p[1]-1, p[2]); };
const fmt = s => s ? s.split('-').reverse().join('/') : '—';
const T0 = parse(D.min), T1 = parse(D.max);
const NDAYS = Math.round((T1 - T0) / DAY) + 1;
const dayIdx = s => s ? (parse(s) - T0) / DAY : NaN;

let showLinks = true;
let px = D.zoom || 4, collapsed = new Set(), vis = [], sel = -1, lastW = 380, first = true;
let linkingSource = null;
let selectedColor = "#5eead4";

const left = $('left'), lbody = $('lbody'), lrows = $('lrows'), right = $('right'),
      rhead = $('rhead'), rbody = $('rbody'), ctxMenu = $('ctxMenu');

function computeVis() {
  vis = []; let skip = -1;
  D.items.forEach((it, k) => {
    if (skip >= 0 && it.d > skip) return;
    skip = -1; vis.push(k);
    if (it.t === 'g' && collapsed.has(k)) skip = it.d;
  });
}

function fit() {
  const h = right.clientHeight - HEAD;
  if (h > 0) lbody.style.height = h + 'px';
}

function barStyle(it) {
  const c = it.c || '#64748b';
  if (!it.p) return 'background:' + c;
  const stripes = 'repeating-linear-gradient(135deg,' + c + ' 0 4px,transparent 4px 9px)';
  const cap = it.p === 1 ? 'linear-gradient(to left,' + c + ' 0 5px,transparent 5px),'
            : it.p === 2 ? 'linear-gradient(to right,' + c + ' 0 5px,transparent 5px),' : '';
  return 'background:' + cap + stripes + ';border:1px solid ' + c;
}

function renderLeft() {
  if (!vis.length) { lrows.innerHTML = '<div id="empty">Sin tareas con estos filtros.</div>'; return; }
  let s = '<div id="lhl"></div><div id="lsel"></div>';
  vis.forEach((k, r) => {
    const it = D.items[k], pad = 8 + it.d * 14;
    const isLinking = (it.t === 't' && it.r === linkingSource);
    if (it.t === 'g') {
      const borderStyle = it.c ? 'border-left: 4px solid ' + it.c + ';' : '';
      s += '<div class="lr g g' + Math.min(it.d, 2) + '" data-r="' + r + '" style="padding-left:' + pad + 'px;' + borderStyle + '" title="' + esc(it.l) + ' (' + it.n + ' tareas)">'
        + '<span class="ar">' + (collapsed.has(k) ? '▸' : '▾') + '</span>'
        + (it.c ? '<i class="dot" style="background:' + it.c + '"></i>' : '')
        + '<span class="lr-content">' + esc(it.l) + '</span><span class="ct">' + it.n + '</span></div>';
    } else {
      const pp = it.q ? '<span class="pp">' + esc(it.q) + '</span>' : '<span class="pp none">sin asignar</span>';
      s += '<div class="lr ' + (isLinking ? 'linking' : '') + '" data-r="' + r + '" data-rid="' + it.r + '" style="padding-left:' + pad + 'px">'
        + '<button class="dots-btn" title="Opciones de enlace" onclick="openMenu(event, ' + it.r + ')">⋮</button>'
        + '<span class="lr-content" title="' + esc(it.l + ' - ' + (it.q || 'sin asignar')) + '">'
        + esc(it.l) + '<span class="sep"> - </span>' + pp
        + (it.x ? '<span class="cx">· ' + esc(it.x) + '</span>' : '')
        + (it.s ? '<span class="sb" title="' + it.s + ' subtareas">↳' + it.s + '</span>' : '')
        + (it.nl ? '<span class="sb" style="color:#5eead4" title="' + it.nl + ' enlaces">🔗' + it.nl + '</span>' : '')
        + '</span></div>';
    }
  });
  lrows.innerHTML = s;
}

function months() {
  const out = []; const d0 = new Date(T0);
  let y = d0.getUTCFullYear(), m = d0.getUTCMonth();
  for (let guard = 0; guard < 600; guard++) {
    const s = Math.max(Date.UTC(y, m, 1), T0);
    if (s > T1) break;
    const e = Math.min(Date.UTC(y, m + 1, 1) - DAY, T1);
    out.push({ s: (s - T0) / DAY, n: (e - s) / DAY + 1, y: y, m: m });
    m++; if (m > 11) { m = 0; y++; }
  }
  return out;
}

function links(W, H) {
  if (!showLinks) return '';
  const row = new Map(); vis.forEach((k, r) => row.set(k, r));
  const byR = {};
  vis.forEach(k => { const it = D.items[k]; if (it.t === 't') (byR[it.r] = byR[it.r] || []).push(k); });
  const cnt = {}; let s = '';
  (D.edges || []).forEach(e => {
    const A = byR[e[0]], B = byR[e[1]];
    if (!A || !B) return;
    B.forEach(kb => {
      const rb = row.get(kb);
      let ka = e[2] === 0 ? A.find(k => k === D.items[kb].pk) : null;
      const cross = e[2] === 0 && ka == null;
      if (ka == null) ka = A.reduce((m, k) => Math.abs(row.get(k) - rb) < Math.abs(row.get(m) - rb) ? k : m, A[0]);
      if (ka === kb) return;
      const p = D.items[ka], c = D.items[kb];
      
      let idxA = dayIdx(p.a), idxB = dayIdx(c.a);
      if (isNaN(idxA)) idxA = dayIdx(p.b) - 1;
      if (isNaN(idxB)) idxB = dayIdx(c.b) - 1;
      if (isNaN(idxA) || isNaN(idxB)) return; // Si no hay fecha en absoluto, omitir trazado

      const color = e[3] || "#5eead4";
      const ra = row.get(ka), yp = ra * RH + 13, yc = rb * RH + 13;
      let g;
      if (e[2] === 0) {
        const xp = Math.max(idxA * px, 10), xc = Math.max(idxB * px, 10), vx = Math.max(Math.min(xp, xc) - 9, 2);
        g = '<path class="ln" style="stroke:' + color + '" d="M' + xp + ' ' + yp + 'H' + vx + 'V' + yc + 'H' + (xc - 6) + '"/>'
          + '<path class="ah" style="fill:' + color + '" d="M' + xc + ' ' + yc + 'l-6 -4v8z"/><circle class="og" style="fill:' + color + '" cx="' + xp + '" cy="' + yp + '" r="2.5"/>';
      } else {
        let idxEndA = dayIdx(p.b);
        if (isNaN(idxEndA)) idxEndA = idxA + 1;
        const xs = Math.max((idxEndA + 1) * px, 10), xt = Math.max(idxB * px, 10), dx = Math.max(30, Math.abs(xt - xs) / 2);
        g = '<path class="ln" style="stroke:' + color + '" d="M' + xs + ' ' + yp + 'C' + (xs + dx) + ' ' + yp + ',' + (xt - 6 - dx) + ' ' + yc + ',' + (xt - 6) + ' ' + yc + '"/>'
          + '<path class="ah" style="fill:' + color + '" d="M' + xt + ' ' + yc + 'l-6 -4v8z"/><circle class="og" style="fill:' + color + '" cx="' + xs + '" cy="' + yp + '" r="2.5"/>';
      }
      s += '<g class="lk' + (cross ? ' x' : '') + '" data-orig="' + e[0] + '" data-dest="' + e[1] + '" data-tipo="' + e[2] + '" onclick="openLinkMenu(event, ' + e[0] + ', ' + e[1] + ', ' + e[2] + ')">' + g + '</g>';
      cnt[ka] = (cnt[ka] || 0) + 1; cnt[kb] = (cnt[kb] || 0) + 1;
    });
  });
  let bd = '';
  Object.keys(cnt).forEach(k => {
    const it = D.items[k];
    let idxB = dayIdx(it.b);
    if (isNaN(idxB)) idxB = dayIdx(it.a);
    if (!isNaN(idxB)) {
      bd += '<div class="bd" title="' + cnt[k] + ' enlaces" style="left:' + (Math.max(idxB, 0) * px + 15) + 'px;top:' + (row.get(+k) * RH + 6) + 'px">🔗' + cnt[k] + '</div>';
    }
  });
  return '<svg id="lk" width="' + W + '" height="' + H + '">' + s + '</svg>' + bd;
}

function renderRight() {
  const W_px = Math.round(NDAYS * px), H = vis.length * RH;
  rhead.style.width = W_px + 'px'; rbody.style.width = W_px + 'px'; rbody.style.height = H + 'px';
  lrows.style.height = H + 'px';
  const ms = months();
  let h = '', lines = '';
  ms.forEach(o => {
    const w = o.n * px;
    h += '<div class="mh" style="left:' + o.s * px + 'px;width:' + w + 'px">' + (w >= 64 ? MES[o.m] + ' ' + o.y : (o.m === 0 ? o.y : MES[o.m])) + '</div>';
    lines += '<div class="vl" style="left:' + o.s * px + 'px"></div>';
  });
  if (px >= 14) {
    for (let i = 0; i < NDAYS; i++) {
      const d = new Date(T0 + i * DAY);
      h += '<div class="dh" style="left:' + i * px + 'px;width:' + px + 'px">' + d.getUTCDate() + '</div>';
    }
  } else if (px >= 5) {
    for (let i = 0; i < NDAYS; i++) {
      const d = new Date(T0 + i * DAY);
      if (d.getUTCDay() === 1) h += '<div class="dh" style="left:' + i * px + 'px;width:' + 7 * px + 'px;text-align:left;padding-left:3px">' + d.getUTCDate() + '</div>';
    }
  }
  rhead.innerHTML = h;
  let s = '<div id="rhl"></div><div id="rsel"></div>' + lines;
  const now = new Date(); const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  if (today >= T0 && today <= T1) s += '<div class="td" title="Hoy" style="left:' + ((today - T0) / DAY * px) + 'px"></div>';
  vis.forEach((k, r) => {
    const it = D.items[k];
    if (!it.a) return;
    const x = Math.max(dayIdx(it.a) * px, 0), w = Math.max((dayIdx(it.b) - dayIdx(it.a) + 1) * px, 4);
    const tip = esc(it.l + ' · Inicio: ' + fmt(it.ra) + ' · Fin: ' + fmt(it.rb));
    const isLinking = (it.t === 't' && it.r === linkingSource);
    if (it.t === 'g') {
      s += '<div class="gb" data-r="' + r + '" title="' + esc(it.l) + '" style="left:' + x + 'px;width:' + w + 'px;top:' + (r * RH + 9) + 'px;background:' + (it.c || '#64748b') + '"></div>';
    } else {
      s += '<div class="bar ' + (isLinking ? 'linking' : '') + '" data-r="' + r + '" data-rid="' + it.r + '" title="' + tip + '" style="left:' + x + 'px;width:' + w + 'px;top:' + (r * RH + 5) + 'px;' + barStyle(it) + '"></div>';
    }
  });
  rbody.innerHTML = s + links(W_px, H);
}

function markSel() {
  const r = vis.indexOf(sel);
  ['lsel', 'rsel'].forEach(id => { const e = $(id); if (e) { e.style.display = r >= 0 ? 'block' : 'none'; e.style.top = r * RH + 'px'; } });
  document.querySelectorAll('#lk .lk').forEach(g =>
    g.classList.toggle('hl', sel >= 0 && (+g.dataset.orig === sel || +g.dataset.dest === sel)));
}

function renderAll() { computeVis(); renderLeft(); renderRight(); markSel(); fit(); }

function hover(r) {
  ['lhl', 'rhl'].forEach(id => { const e = $(id); if (e) { e.style.display = r >= 0 ? 'block' : 'none'; e.style.top = r * RH + 'px'; } });
}

function showDetail(k) {
  const it = D.items.find(item => item.r === k && item.t === 't');
  if (!it) return;
  const el = $('detail');
  el.innerHTML = '<div class="dt"><b>' + esc(it.l) + '</b><span>' + esc(it.q || 'sin asignar') + '</span>'
    + '<button id="dx">✕ Cerrar</button></div>'
    + (it.o ? '<div class="dn">⚠️ ' + esc(it.o) + '</div>' : '')
    + (it.e && it.e.length ? '<div class="dn" style="color:#8fe3d4">🔗 ' + it.e.map(esc).join(' · ') + '</div>' : '')
    + '<div class="dg">' + (it.f || []).map(p => '<div><span>' + esc(p[0]) + '</span>' + esc(p[1]) + '</div>').join('') + '</div>';
  document.body.classList.add('det');
  $('dx').onclick = () => { document.body.classList.remove('det'); sel = -1; markSel(); fit(); };
  fit();
}

function activate(r) {
  const k = vis[r]; if (k == null) return;
  const it = D.items[k];
  if (it.t === 'g') {
    if (collapsed.has(k)) collapsed.delete(k); else collapsed.add(k);
    renderAll();
  } else { sel = it.r; markSel(); showDetail(it.r); }
}

// ---- INTERACCIÓN DINÁMICA DE ENLACES CON CAMBIO DE COLOR EN TIEMPO REAL ----
window.addEventListener('click', () => { ctxMenu.style.display = 'none'; });

window.openMenu = function(e, rid) {
  e.stopPropagation();
  let html = '';
  if (linkingSource === null) {
    html += '<div class="citem" onclick="startLinking(' + rid + ')">🔗 Iniciar enlace desde esta tarea</div>';
  } else {
    if (linkingSource !== rid) {
      html += '<div class="citem" onclick="createLinkTo(' + rid + ')">🔗 Enlazar con esta tarea</div>';
    }
    html += '<div class="citem" onclick="stopLinking()">🛑 Terminar enlaces</div>';
  }

  ctxMenu.innerHTML = html;
  const rect = e.target.getBoundingClientRect();
  ctxMenu.style.left = rect.left + 'px';
  ctxMenu.style.top = (rect.bottom + 4) + 'px';
  ctxMenu.style.display = 'block';
}

window.openLinkMenu = function(e, orig, dest, tipo) {
  e.stopPropagation();
  let colors = ["#5eead4", "#facc15", "#f97316", "#3b82f6", "#ec4899", "#22c55e"];
  let html = '<div class="citem" style="color:#ef4444;" onclick="removeLink(' + orig + ', ' + dest + ', ' + tipo + ')">❌ Eliminar enlace</div>';
  html += '<div style="padding:4px 14px 2px;color:#9aa4b5;font-size:11px;">Cambiar color:</div><div class="color-picker">';
  colors.forEach(c => {
    html += '<div class="color-opt" style="background:' + c + '" onclick="changeLinkColor(' + orig + ', ' + dest + ', ' + tipo + ', \'' + c + '\')"></div>';
  });
  html += '</div>';

  ctxMenu.innerHTML = html;
  ctxMenu.style.left = e.clientX + 'px';
  ctxMenu.style.top = e.clientY + 'px';
  ctxMenu.style.display = 'block';
}

window.startLinking = function(rid) {
  linkingSource = rid;
  ctxMenu.style.display = 'none';
  renderAll();
}

window.createLinkTo = function(destRid) {
  if (linkingSource !== null) {
    if (!D.edges) D.edges = [];
    const existe = D.edges.some(edge => edge[0] === linkingSource && edge[1] === destRid);
    if (!existe) {
      D.edges.push([linkingSource, destRid, 1, selectedColor]);
    }
    ctxMenu.style.display = 'none';
    renderAll();
  }
}

window.changeLinkColor = function(orig, dest, tipo, newColor) {
  if (D.edges) {
    const edge = D.edges.find(e => e[0] === orig && e[1] === dest && e[2] === tipo);
    if (edge) edge[3] = newColor;
  }
  ctxMenu.style.display = 'none';
  renderAll();
}

window.removeLink = function(orig, dest, tipo) {
  if (D.edges) {
    D.edges = D.edges.filter(edge => !(edge[0] === orig && edge[1] === dest && edge[2] === tipo));
  }
  ctxMenu.style.display = 'none';
  renderAll();
}

window.stopLinking = function() {
  linkingSource = null;
  ctxMenu.style.display = 'none';
  renderAll();
}

const rowFromY = e => Math.floor((e.clientY - rbody.getBoundingClientRect().top) / RH);
rbody.addEventListener('mousemove', e => { const r = rowFromY(e); hover(r >= 0 && r < vis.length ? r : -1); });
rbody.addEventListener('mouseleave', () => hover(-1));
rbody.addEventListener('click', e => { 
  if (e.target.closest('.lk')) return;
  const r = rowFromY(e); if (r >= 0 && r < vis.length) activate(r); 
});
lrows.addEventListener('mousemove', e => { const n = e.target.closest('.lr'); hover(n ? +n.dataset.r : -1); });
lrows.addEventListener('mouseleave', () => hover(-1));
lrows.addEventListener('click', e => { 
  if (e.target.closest('.dots-btn')) return;
  const n = e.target.closest('.lr'); if (n) activate(+n.dataset.r); 
});

right.addEventListener('scroll', () => { lrows.style.transform = 'translateY(' + (-right.scrollTop) + 'px)'; });
lbody.addEventListener('wheel', e => { right.scrollTop += e.deltaY; right.scrollLeft += e.deltaX; e.preventDefault(); }, { passive: false });
window.addEventListener('resize', fit);

const split = $('split'); let drag = false;
function setW(w) {
  const mw = $('main').clientWidth;
  w = Math.max(0, Math.min(w, mw - 140));
  left.style.width = w + 'px'; fit();
}
split.addEventListener('pointerdown', e => { drag = true; split.setPointerCapture(e.pointerId); document.body.classList.add('dr'); e.preventDefault(); });
split.addEventListener('pointermove', e => { if (drag) setW(e.clientX - $('main').getBoundingClientRect().left - 4); });
const stop = e => { if (!drag) return; drag = false; document.body.classList.remove('dr'); if (left.offsetWidth > 20) lastW = left.offsetWidth; };
split.addEventListener('pointerup', stop); split.addEventListener('pointercancel', stop);
function togglePanel() { if (left.offsetWidth > 20) { lastW = left.offsetWidth; setW(0); } else setW(lastW || 380); }
split.addEventListener('dblclick', togglePanel);
$('btnPanel').onclick = togglePanel;

function scrollToday() {
  const now = new Date(); const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  if (today < T0 || today > T1) return;
  right.scrollLeft = Math.max(0, (today - T0) / DAY * px - right.clientWidth / 3);
}
function setZoom(v) {
  const cx = (right.scrollLeft + right.clientWidth / 2) / px;
  px = v; $('zv').textContent = v + ' px/día';
  renderRight(); markSel();
  right.scrollLeft = Math.max(0, cx * px - right.clientWidth / 2);
}
$('zm').value = px; $('zv').textContent = px + ' px/día';
$('zm').oninput = e => setZoom(+e.target.value);
$('btnToday').onclick = scrollToday;
$('btnLk').onclick = () => { showLinks = !showLinks; $('btnLk').textContent = '🔗 Relaciones: ' + (showLinks ? 'sí' : 'no'); renderRight(); markSel(); };
$('btnCol').onclick = () => { collapsed = new Set(); D.items.forEach((it, k) => { if (it.t === 'g') collapsed.add(k); }); renderAll(); };
$('btnExp').onclick = () => { collapsed = new Set(); renderAll(); };
const HAS_LK = (D.edges || []).length > 0;
$('legend').innerHTML = (D.legend || []).map(l => '<span class="chip"><i style="background:' + l[1] + '"></i>' + esc(l[0]) + '</span>').join('')
  + '<span class="chip" title="Tareas sin fecha de inicio o de fin"><i style="background:repeating-linear-gradient(135deg,#aab 0 3px,transparent 3px 6px);border:1px solid #aab"></i>Rayado = fechas incompletas</span>'
  + (HAS_LK ? '<span class="chip" title="Madre → subtarea"><i style="background:none;border-bottom:2px solid #e2e8f0;border-radius:0;height:6px"></i>Madre → subtarea</span>'
    + '<span class="chip" title="Enlace entre tareas"><i style="background:none;border-bottom:2px solid #5eead4;border-radius:0;height:6px"></i>Enlace entre tareas</span>' : '');

renderAll();
if (first) { first = false; scrollToday(); }
</script>
</body></html>
"""


def mostrar_html(html, alto):
    if hasattr(st, "iframe"):
        st.iframe(html, height=alto)
    else:
        import streamlit.components.v1 as components

        components.html(html, height=alto, scrolling=False)


def vista_gantt():
    st.subheader("📈 Diagrama de Gantt")

    if not (col_start_sel and col_end_sel):
        st.info("ℹ️ Elige las columnas de fecha de inicio y de fin en «Columnas detectadas».")
        return
    if col_start_sel == col_end_sel:
        st.warning("⚠️ La fecha de inicio y la de fin son la misma columna.")
        return

    disp = {}
    for nombre, col in [
        ("Departamento", col_dep_sel), ("Carpeta", col_folder_sel), ("Lista", col_list_sel),
        ("Persona", col_persona_sel), ("Urgencia", col_prio_sel), ("Estado", col_estado_sel),
    ]:
        if col:
            disp[nombre] = col
    opciones_color = [n for n in ("Departamento", "Carpeta", "Lista", "Urgencia", "Estado") if n in disp]
    if not opciones_color:
        st.info("ℹ️ Asigna al menos una columna en «Columnas detectadas».")
        return

    sig = f"{file_key}_{'|'.join(disp)}"
    c1, c2, c3 = st.columns([3, 2, 2])
    niveles = c1.multiselect(
        "🗂️ Agrupar por (en este orden)", list(disp),
        default=[n for n in ("Departamento", "Carpeta", "Lista") if n in disp],
        key=f"g_niv_{sig}",
    )
    def_col_idx = opciones_color.index("Carpeta") if "Carpeta" in opciones_color else 0
    color_nivel = c2.selectbox("🎨 Colorear por", opciones_color, index=def_col_idx, key=f"g_col_{sig}")
    orden_t = c3.selectbox(
        "↕️ Ordenar tareas dentro de cada grupo", ["Fecha de inicio", "Fecha de fin", "Nombre"], key=f"g_ord_{sig}"
    )
    d1, d2 = st.columns([2, 3])
    ver_sin = d1.checkbox("Mostrar tareas sin ninguna fecha (rayadas)", value=True, key=f"g_sin_{sig}")
    alto = d2.slider("Alto del diagrama (px)", 400, 1200, 760, step=20, key=f"g_alt_{sig}")

    config = {}
    edges = construir_enlaces(df, config, col_id_sel, col_tarea_sel, st.session_state.enlaces_manuales, st.session_state.enlaces_eliminados)

    datos = construir_datos_gantt(niveles, disp, color_nivel, orden_t, ver_sin, edges)
    if datos is None:
        st.warning("⚠️ No hay ninguna fecha válida en el CSV.")
        return
    if not datos["items"]:
        st.warning("⚠️ No hay tareas con los filtros actuales.")
        return

    st.caption("✨ **Instrucciones:** Haz clic en los **tres puntitos (`⋮`)** de una tarea para enlazarla con otra. Haz clic en **cualquier flecha** para eliminarla o cambiarle el color al instante.")

    html = GANTT_HTML.replace("__DATA__", json.dumps(datos, ensure_ascii=False).replace("</", "<\\/"))
    mostrar_html(html, alto)


if modo_vista == VISTA_TABLA:
    vista_tabla()
else:
    vista_gantt()