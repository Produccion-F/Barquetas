# -*- coding: utf-8 -*-
import streamlit as st
import time
import pandas as pd
import datetime
from collections import deque
import altair as alt
import math
import copy
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

# --- Configuración de la página ---
st.set_page_config(
    page_title="Gemelo Digital: Sala de embarquetado V.2",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==========================================
# 0. ESTILOS VISUALES (CSS ORIGINAL RESTAURADO)
# ==========================================
st.markdown("""
<style>
    /* FONDO Y TÍTULOS */
    .stApp { background-color: #ecf0f1; }
    h1 { color: #2980b9; text-align: center; font-size: 3rem !important; margin-bottom: 2rem; font-weight: 800; }
    h3 { color: #2980b9 !important; font-size: 1.8rem !important; font-weight: 600; margin-top: 1rem; }

    /* ESTILOS DE TABLA (AgGrid y Nativas) */
    .stDataFrame th, .ag-header-cell-label {
        text-align: left !important;
        justify-content: flex-start !important;
        font-weight: 900 !important;
        color: #000000 !important;
        font-size: 15px !important;
    }
    .stDataFrame td, .ag-cell {
        text-align: left !important;
        font-size: 14px !important;
        display: flex;
        align-items: center;
    }

    /* TARJETAS DE MÉTRICAS */
    div[data-testid="stMetric"] {
        background-color: #ffffff;
        padding: 1rem;
        border-radius: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        text-align: center;
        border: 1px solid #e0e0e0;
    }
    div[data-testid="stMetricValue"] { font-size: 1.8rem !important; color: #3498db; font-weight: bold; }
    div[data-testid="stMetricLabel"] { font-size: 1rem !important; color: #2c3e50; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 1. FUNCIONES HELPER
# ==========================================

def fmt_num_es(val):
    """Formato español 1.000,00"""
    if pd.isna(val) or val == "": return ""
    try:
        if val == int(val): return "{:,.0f}".format(val).replace(",", ".")
        return "{:,.2f}".format(val).replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(val)

def render_aggrid(df, key_id, height=None):
    if df.empty:
        st.info("Sin datos.")
        return
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(resizable=True, filterable=True, sortable=True, editable=False, cellStyle={'textAlign': 'left'}, headerClass='ag-header-cell-label')
    gb.configure_grid_options(domLayout='autoHeight')
    AgGrid(df, gridOptions=gb.build(), height=height if height else 200, width='100%', fit_columns_on_grid_load=True, theme='streamlit', key=key_id)

def time_to_float(time_obj):
    if time_obj is None: return 0.0
    return time_obj.hour + time_obj.minute / 60.0

def float_to_time_str(time_float):
    hours = int(time_float); minutes = round((time_float - hours) * 60)
    if minutes == 60: hours += 1; minutes = 0
    days = hours // 24; hours = hours % 24
    if days > 0: return f"{hours:02d}:{minutes:02d} (+{days}d)"
    return f"{hours:02d}:{minutes:02d}"

def str_to_time(time_str):
    if not time_str or pd.isna(time_str): return None
    try: return datetime.datetime.strptime(str(time_str).strip(), '%H:%M').time()
    except ValueError: return None

def str_to_bool(val):
    if pd.isna(val): return False
    s = str(val).strip().upper()
    return s in ['TRUE', 'VERDADERO', 'SI', 'YES', '1']

def safe_get_int(val, default):
    if pd.isna(val) or val == "": return default
    s_val = str(val).strip().replace('.', '').replace(',', '')
    if s_val == '': return default
    try: return int(float(s_val))
    except: return default

def calcular_descanso_en_tramo(tramo_start_float, tramo_end_float, breaks_config):
    total_descanso = 0.0
    for i in range(1, 4):
        if not breaks_config[f'skip_{i}']:
            s = breaks_config[f'start_{i}']; e = breaks_config[f'end_{i}']
            if s == 0.0 and e == 0.0: continue
            overlap = max(0, min(tramo_end_float, e) - max(tramo_start_float, s))
            total_descanso += overlap
    return total_descanso

# ==========================================
# 2. CARGA DE DATOS (BLINDADA)
# ==========================================
def load_data_from_sheets():
    if "google_sheet_config" not in st.secrets:
        st.error("Faltan los 'Secrets'.")
        return

    try:
        url_config = st.secrets["google_sheet_config"]["config_lineas_url"]
        url_plan = st.secrets["google_sheet_config"]["plan_produccion_url"]

        # Lectura robusta
        df_config = pd.read_csv(url_config, dtype=str, keep_default_na=False, on_bad_lines='skip', encoding='utf-8-sig', engine='python')
        df_plan = pd.read_csv(url_plan, dtype=str, keep_default_na=False, on_bad_lines='skip', encoding='utf-8-sig', engine='python')

        # --- DETECCIÓN DE ERROR HTML ---
        if not df_config.empty and str(df_config.iloc[0,0]).strip().startswith("<"):
            st.error("🚨 ERROR CRÍTICO: Tus enlaces son PÁGINAS WEB, no CSV.")
            st.info("Por favor, revisa el paso 1 de la respuesta y actualiza los secretos.")
            st.stop()

        # Limpieza
        df_config.columns = df_config.columns.str.strip().str.replace('"', '')
        df_plan.columns = df_plan.columns.str.strip().str.replace('"', '')

        # Autocorrección linea_id
        if 'linea_id' not in df_config.columns:
             for c in df_config.columns:
                 if 'linea' in c.lower() and 'id' in c.lower():
                     df_config.rename(columns={c: 'linea_id'}, inplace=True); break

        st.session_state.df_config_raw = df_config
        st.session_state.df_plan_raw = df_plan
        st.session_state.lineas_configuradas = []

        if 'turno' not in df_config.columns: df_config['turno'] = '1'

        # CONFIGURACIÓN
        for index, row in df_config.iterrows():
            linea_id = str(row.get('linea_id', '')).strip()
            t_raw = str(row.get('turno', '1')).split('.')[0].strip()
            turno = t_raw if t_raw else '1'
            if not linea_id: continue

            prefijo = f"l{linea_id}_t{turno}_"
            st.session_state.lineas_configuradas.append((linea_id, turno))
            st.session_state[f"{prefijo}oee"] = safe_get_int(row.get('oee_global'), 85)
            st.session_state[f"{prefijo}hora_inicio"] = str_to_time(row.get('hora_inicio')) or datetime.time(8, 0)
            for i in range(1, 4):
                st.session_state[f"{prefijo}desc_{i}_start"] = str_to_time(row.get(f'desc_{i}_inicio'))
                st.session_state[f"{prefijo}desc_{i}_end"] = str_to_time(row.get(f'desc_{i}_fin'))
                st.session_state[f"{prefijo}desc_{i}_skip"] = str_to_bool(row.get(f'desc_{i}_skip', 'FALSE'))

        # PLAN
        if 'turno' not in df_plan.columns: df_plan['turno'] = '1'
        if 'hora_entrada_cliente' not in df_plan.columns: df_plan['hora_entrada_cliente'] = ""

        df_plan['hora_validada'] = df_plan['hora_entrada_cliente'].apply(str_to_time)

        for col in ['linea_id', 'turno', 'nombre_cliente', 'nombre_articulo']:
             if col in df_plan.columns:
                df_plan[col] = df_plan[col].astype(str).apply(lambda x: x.split('.')[0] if x.replace('.','',1).isdigit() else x).str.strip()

        horas_por_cliente = df_plan.groupby(['linea_id', 'turno', 'nombre_cliente'])['hora_validada'].last()
        clientes_por_linea_turno = {}

        for index, row in df_plan.iterrows():
            linea_id = row.get('linea_id'); turno = row.get('turno')
            if not turno: turno = '1'
            cliente = row.get('nombre_cliente')
            if not linea_id or not cliente: continue

            key_lt = f"{linea_id}_{turno}"
            if key_lt not in clientes_por_linea_turno: clientes_por_linea_turno[key_lt] = {}

            if cliente not in clientes_por_linea_turno[key_lt]:
                hora_obj = None
                try:
                    if (linea_id, turno, cliente) in horas_por_cliente.index:
                        hora_obj = horas_por_cliente.get((linea_id, turno, cliente))
                except: pass
                act_hora = str_to_bool(row.get('act_hora_entrada', 'FALSE'))
                clientes_por_linea_turno[key_lt][cliente] = {"nombre": cliente, "articulos": [], "hora_entrada": hora_obj, "tiene_hora": (hora_obj is not None) and act_hora}

            nombre_art = str(row.get('nombre_articulo', ''))
            if nombre_art:
                oee_linea = st.session_state.get(f"l{linea_id}_t{turno}_oee", 85)
                oee_art = oee_linea
                if str_to_bool(row.get('act_oee_articulo', 'FALSE')):
                    oee_art = safe_get_int(row.get('oee_articulo'), oee_linea)

                art_obj = {"nombre": nombre_art, "cantidad": safe_get_int(row.get('barquetas_pedido'), 0), "velocidad": safe_get_int(row.get('velocidad_estimada'), 800), "oee": oee_art}
                clientes_por_linea_turno[key_lt][cliente]['articulos'].append(art_obj)

        st.session_state.plan_data = clientes_por_linea_turno
        # st.toast('✅ Datos cargados.', icon="📥") # Toast opcional

    except Exception as e:
        st.error(f"Error al cargar datos: {e}")

# ==========================================
# 3. MOTOR SIMULACIÓN
# ==========================================
def setup_simulation_instance(lid, turno):
    pref = f"l{lid}_t{turno}_"
    start_time_obj = st.session_state.get(f"{pref}hora_inicio")
    if not start_time_obj: return None
    start_time = time_to_float(start_time_obj)

    breaks = {}; breaks_desc = []
    for i in range(1, 4):
        s_obj = st.session_state.get(f"{pref}desc_{i}_start")
        e_obj = st.session_state.get(f"{pref}desc_{i}_end")
        skip = st.session_state.get(f"{pref}desc_{i}_skip", True)
        s = time_to_float(s_obj) if s_obj else 0.0
        e = time_to_float(e_obj) if e_obj else 0.0
        breaks[f'start_{i}'] = s; breaks[f'end_{i}'] = e; breaks[f'skip_{i}'] = skip
        if not skip and s_obj and e_obj: breaks_desc.append(f"{s_obj.strftime('%H:%M')}-{e_obj.strftime('%H:%M')}")

    key_lt = f"{lid}_{turno}"
    clientes_data = st.session_state.get("plan_data", {}).get(key_lt, {})
    queue = deque(); total_obj = 0; resumen_tabla = []

    for c_name, c_data in clientes_data.items():
        h_ent = time_to_float(c_data['hora_entrada']) if c_data['tiene_hora'] else 0.0
        for art in c_data['articulos']:
            cant = art['cantidad']
            if cant > 0:
                vel_real = art['velocidad'] * (art['oee'] / 100.0)
                if vel_real <= 0: vel_real = 1.0
                horas_est = cant / vel_real
                queue.append({"cliente": c_name, "articulo": art['nombre'], "pendiente": cant, "vel_real": vel_real, "hora_entrada": h_ent})
                total_obj += cant
                resumen_tabla.append({"Cliente": c_name, "Artículo": art['nombre'], "Pedido": fmt_num_es(cant), "Vel. Teórica": fmt_num_es(art['velocidad']), "OEE": f"{art['oee']}%", "Vel. Real": fmt_num_es(vel_real), "Horas Est.": fmt_num_es(horas_est) + " h"})

    if total_obj == 0: return None
    return {"id": lid, "turno": turno, "active": True, "start_time": start_time, "queue": queue, "current_job": None, "end_time": None, "interrupted": False, "producido": 0, "horas_netas": 0.0, "horas_descanso": 0.0, "breaks": breaks, "breaks_desc": ", ".join(breaks_desc), "total_obj": total_obj, "history": [], "resumen_tabla": pd.DataFrame(resumen_tabla)}

def run_sim_tick(sim, current_global_time, step=1.0):
    if not sim['active']: return 0, 0
    t_start = max(sim['start_time'], current_global_time)
    t_end = current_global_time + step
    if t_start >= t_end: return 0, 0

    day_start, day_end = t_start % 24, t_end % 24
    if day_end < day_start: day_end += 24
    descanso = calcular_descanso_en_tramo(day_start, day_end, sim['breaks'])
    net_duration = max(0, (t_end - t_start) - descanso)
    produced_in_tick = 0; worked_in_tick = 0.0

    while worked_in_tick < net_duration:
        if not sim['current_job']:
            if not sim['queue']: sim['active'] = False; sim['end_time'] = t_start + worked_in_tick + descanso; break
            next_job = sim['queue'][0]
            current_abs = t_start + worked_in_tick + descanso
            if next_job['hora_entrada'] > (current_abs % 24) and current_abs < 24: break
            sim['current_job'] = sim['queue'].popleft()

        job = sim['current_job']
        time_needed = job['pendiente'] / job['vel_real']
        time_step = min(time_needed, net_duration - worked_in_tick)
        qty = time_step * job['vel_real']
        job['pendiente'] -= qty; produced_in_tick += qty; worked_in_tick += time_step; sim['producido'] += qty
        if job['pendiente'] < 0.1: sim['current_job'] = None

    sim['horas_netas'] += worked_in_tick; sim['horas_descanso'] += descanso
    if produced_in_tick > 0 or sim['active']: sim['history'].append({"Hora": int(t_end), "Prod": produced_in_tick, "Acum": sim['producido']})
    if sim['active']: sim['end_time'] = t_end
    return produced_in_tick, descanso

# ==========================================
# 4. INICIALIZACIÓN Y UI
# ==========================================
if 'segundos_por_hora_sim' not in st.session_state: st.session_state.segundos_por_hora_sim = 0.25
if 'activar_t2' not in st.session_state: st.session_state.activar_t2 = False

# CARGA INICIAL OBLIGATORIA
load_data_from_sheets()

st.markdown("<h1 style='text-align: center;'>🏭 Simulador Multi-Turno</h1>", unsafe_allow_html=True)
tab_cfg, tab_sim = st.tabs(["⚙️ Configuración", "🚀 Simulación"])

# --- CONFIGURACIÓN ---
with tab_cfg:
    def dibujar_config_linea(linea_id, turno):
        prefijo = f"l{linea_id}_t{turno}_"
        if f"{prefijo}hora_inicio" not in st.session_state: return
        titulo = f"🥩 Configuración Línea {linea_id}";
        if turno == '2': titulo += " (Turno 2)"

        with st.expander(titulo, expanded=False):
            with st.expander(f"Ver Parámetros de Horarios", expanded=False):
                st.markdown("### 🕒 Horario")
                h_ini = st.session_state[f"{prefijo}hora_inicio"]
                st.text_input(f"Hora Inicio", value=h_ini.strftime('%H:%M'), disabled=True, key=f"{prefijo}ui_h_ini")
                st.markdown("### ⏱️ Descansos Activos")
                any_break = False
                for i in range(1, 4):
                    if not st.session_state[f"{prefijo}desc_{i}_skip"]:
                        s = st.session_state.get(f"{prefijo}desc_{i}_start")
                        e = st.session_state.get(f"{prefijo}desc_{i}_end")
                        if s and e: st.caption(f"**Descanso {i}:** {s.strftime('%H:%M')} - {e.strftime('%H:%M')}"); any_break = True
                if not any_break: st.caption("Sin descansos programados.")
            st.markdown("---")
            st.markdown(f"### 🏭 OEE Global: `{st.session_state[f'{prefijo}oee']}%`")
            st.markdown("### 📋 Plan de Producción")
            key_lt = f"{linea_id}_{turno}"
            clientes = st.session_state.get("plan_data", {}).get(key_lt, {})
            if not clientes: st.warning("⚠️ No hay pedidos cargados para esta línea.")
            else:
                for c_name, c_data in clientes.items():
                    with st.expander(f"👤 **{c_name}**", expanded=True):
                        if c_data['tiene_hora']: st.success(f"🕒 Hora entrada pedido: {c_data['hora_entrada'].strftime('%H:%M')}")
                        rows = []
                        for art in c_data['articulos']:
                            rows.append({"Artículo": art['nombre'], "Cantidad": fmt_num_es(art['cantidad']), "Velocidad": fmt_num_es(art['velocidad']), "OEE (%)": f"{art['oee']}%"})
                        render_aggrid(pd.DataFrame(rows), f"grid_cfg_{linea_id}_{turno}_{c_name}")

    st.markdown("### 🎛️ Panel de Control")
    c1, c2 = st.columns(2)
    c1.slider("Velocidad Simulación", 0.0, 2.0, key="segundos_por_hora_sim")
    c2.checkbox("Activar Segundo Turno (T2)", key="activar_t2")
    if st.button("🔄 Recargar datos"):
        load_data_from_sheets()
        st.rerun()
    st.markdown("---")
    if 'lineas_configuradas' in st.session_state:
        lineas_ord = sorted(st.session_state.lineas_configuradas, key=lambda x: (x[1], x[0]))
        for lid, turno in lineas_ord:
            if turno == '2' and not st.session_state.activar_t2: continue
            dibujar_config_linea(lid, turno)
    else: st.warning("No se encontraron líneas en la configuración. Revisa el Excel.")

# --- SIMULACIÓN ---
with tab_sim:
    if st.button("▶️ EJECUTAR SIMULACIÓN", type="primary"):
        sims = []; start_t2_map = {}
        if 'lineas_configuradas' in st.session_state:
            if st.session_state.activar_t2:
                for lid, turno in st.session_state.lineas_configuradas:
                    if turno == '2':
                        pref = f"l{lid}_t{turno}_"
                        t_obj = st.session_state.get(f"{pref}hora_inicio")
                        if t_obj: start_t2_map[lid] = time_to_float(t_obj)
            for lid, turno in st.session_state.lineas_configuradas:
                if turno == '2' and not st.session_state.activar_t2: continue
                s = setup_simulation_instance(lid, turno)
                if s: sims.append(s)

        if not sims:
            st.error("No hay pedidos cargados.")
        else:
            global_start = min(s['start_time'] for s in sims)
            curr_t = global_start
            ph_global_metrics = st.empty()
            st.markdown("---")
            st.markdown("##### Producción Global - Turno 1")
            ph_global_chart_t1 = st.empty()
            ph_global_chart_t2 = None
            if st.session_state.activar_t2:
                st.markdown("##### Producción Global - Turno 2")
                ph_global_chart_t2 = st.empty()
            st.markdown("---")
            ph_lines = {}
            for turno_n in ['1', '2']:
                sims_t = [s for s in sims if s['turno'] == turno_n]
                if sims_t:
                    st.markdown(f"#### {'☀️' if turno_n=='1' else '🌙'} Turno {turno_n}")
                    for i in range(0, len(sims_t), 2):
                        cols = st.columns(2)
                        with cols[0]:
                            s = sims_t[i]
                            st.markdown(f"**Línea {s['id']}**")
                            ph_lines[f"{s['id']}_{s['turno']}"] = st.empty()
                        if i + 1 < len(sims_t):
                            with cols[1]:
                                s = sims_t[i+1]
                                st.markdown(f"**Línea {s['id']}**")
                                ph_lines[f"{s['id']}_{s['turno']}"] = st.empty()

            global_hist_t1 = []; global_hist_t2 = []; running = True; acc_t1 = 0; acc_t2 = 0
            last_active_hour_t1 = global_start; last_active_hour_t2 = global_start
            start_t2_hour = 24
            if st.session_state.activar_t2 and start_t2_map:
                 if start_t2_map: start_t2_hour = min(start_t2_map.values())

            while running:
                running = False; curr_t += 1.0; prod_tick_t1 = 0; prod_tick_t2 = 0; fin_t1_val = 0; fin_t2_val = 0; t1_active_now = False; t2_active_now = False
                for s in sims:
                    if s['turno'] == '1' and s['active'] and s['id'] in start_t2_map:
                         if curr_t > start_t2_map[s['id']]: s['active'] = False; s['interrupted'] = True; s['end_time'] = start_t2_map[s['id']]
                    p, d = run_sim_tick(s, curr_t - 1.0)
                    if s['turno'] == '1': prod_tick_t1 += p;
                    else: prod_tick_t2 += p;
                    if s['active']:
                        running = True
                        if s['turno'] == '1': t1_active_now = True
                        else: t2_active_now = True
                    current_end = s['end_time'] if s['end_time'] else (s['start_time'] + s['horas_netas'] + s['horas_descanso'])
                    if s['turno'] == '1' and current_end > fin_t1_val: fin_t1_val = current_end
                    if s['turno'] == '2' and current_end > fin_t2_val: fin_t2_val = current_end

                    ph = ph_lines.get(f"{s['id']}_{s['turno']}")
                    if ph:
                        with ph.container():
                            c1, c2, c3 = st.columns([1, 1, 2])
                            c1.metric("Neto", f"{s['horas_netas']:.1f}h"); c2.metric("Desc", f"{s['horas_descanso']:.1f}h"); c3.write(f"**{fmt_num_es(s['producido'])} / {fmt_num_es(s['total_obj'])}**")
                            if s['history']:
                                df_h = pd.DataFrame(s['history'])
                                base = alt.Chart(df_h).encode(x=alt.X('Hora:O', axis=alt.Axis(labels=True, title='Hora')))
                                bar = base.mark_bar(color='#29b5e8').encode(y=alt.Y('Prod:Q', axis=alt.Axis(title='Prod/h', orient='left')), tooltip=['Hora', 'Prod'])
                                txt_bar = bar.mark_text(dy=-5, color='black').encode(text=alt.Text('Prod:Q', format=',.0f'))
                                line = base.mark_line(color='#ff8c00').encode(y=alt.Y('Acum:Q', axis=alt.Axis(title='Acum', orient='right')))
                                txt_line = line.mark_text(dy=-10, color='#e6550d').encode(text=alt.Text('Acum:Q', format=',.0f'))
                                st.altair_chart((bar + txt_bar + line + txt_line).resolve_scale(y='independent').properties(height=250), use_container_width=True)

                acc_t1 += prod_tick_t1; acc_t2 += prod_tick_t2
                if t1_active_now or prod_tick_t1 > 0: last_active_hour_t1 = curr_t
                if t2_active_now or prod_tick_t2 > 0: last_active_hour_t2 = curr_t
                global_hist_t1.append({"Hora": int(curr_t), "Prod": prod_tick_t1, "Acum": acc_t1})
                global_hist_t2.append({"Hora": int(curr_t), "Prod": prod_tick_t2, "Acum": acc_t2})

                with ph_global_metrics.container():
                    k1, k2, k3, k4, k5 = st.columns(5)
                    k1.metric("Fin Turno 1", float_to_time_str(fin_t1_val) if fin_t1_val > 0 else "-"); k2.metric("Total T1", fmt_num_es(acc_t1)); k3.metric("Fin Turno 2", float_to_time_str(fin_t2_val) if fin_t2_val > 0 else "-"); k4.metric("Total T2", fmt_num_es(acc_t2)); k5.metric("Total Global", fmt_num_es(acc_t1 + acc_t2))

                with ph_global_chart_t1.container():
                    if acc_t1 > 0:
                        df_g1 = pd.DataFrame(global_hist_t1); limit_t1 = start_t2_hour if st.session_state.activar_t2 else last_active_hour_t1
                        df_g1 = df_g1[df_g1['Hora'] <= limit_t1]
                        base = alt.Chart(df_g1).encode(x=alt.X('Hora:O', axis=alt.Axis(labels=True)))
                        bar = base.mark_bar(color='#1f77b4').encode(y=alt.Y('Prod:Q', axis=alt.Axis(title='Prod/h', orient='left')))
                        txt_b = bar.mark_text(dy=-5).encode(text=alt.Text('Prod:Q', format='.0f'))
                        line = base.mark_line(color='#ff7f0e').encode(y=alt.Y('Acum:Q', axis=alt.Axis(title='Total', orient='right')))
                        txt_l = line.mark_text(dy=-10, color='#ff7f0e').encode(text=alt.Text('Acum:Q', format='.0f'))
                        st.altair_chart((bar + txt_b + line + txt_l).resolve_scale(y='independent').properties(height=250, title="GLOBAL TURNO 1"), use_container_width=True)

                if ph_global_chart_t2 and acc_t2 > 0:
                    with ph_global_chart_t2.container():
                        df_g2 = pd.DataFrame(global_hist_t2); df_g2 = df_g2[df_g2['Hora'] >= start_t2_hour]; df_g2 = df_g2[df_g2['Hora'] <= last_active_hour_t2]
                        base = alt.Chart(df_g2).encode(x=alt.X('Hora:O', axis=alt.Axis(labels=True)))
                        bar = base.mark_bar(color='#2ca02c').encode(y=alt.Y('Prod:Q', axis=alt.Axis(title='Prod/h', orient='left')))
                        txt_b = bar.mark_text(dy=-5).encode(text=alt.Text('Prod:Q', format='.0f'))
                        line = base.mark_line(color='#d62728').encode(y=alt.Y('Acum:Q', axis=alt.Axis(title='Total', orient='right')))
                        txt_l = line.mark_text(dy=-10, color='#d62728').encode(text=alt.Text('Acum:Q', format='.0f'))
                        st.altair_chart((bar + txt_b + line + txt_l).resolve_scale(y='independent').properties(height=250, title="GLOBAL TURNO 2"), use_container_width=True)

                time.sleep(st.session_state.segundos_por_hora_sim)
                if curr_t > global_start + 48: break

            st.success("✅ Simulación Completada")
            st.markdown("---")
            st.markdown("### 📈 Resumen Global")
            resumen_final = []; total_neto_planta = 0
            for s in sims:
                fin = s['end_time'] if s['end_time'] else (s['start_time'] + s['horas_netas'] + s['horas_descanso'])
                total_neto_planta += s['horas_netas']; nota = " (Cortado)" if s.get('interrupted') else ""
                resumen_final.append({"Línea/Turno": f"L{s['id']} (T{s['turno']})", "Barquetas Totales": fmt_num_es(s['producido']), "Horas Netas": fmt_num_es(s['horas_netas']), "Horas Descanso": fmt_num_es(s['horas_descanso']), "Hora Fin": float_to_time_str(fin) + nota})

            resumen_final.append({"Línea/Turno": "GLOBAL", "Barquetas Totales": fmt_num_es(acc_t1 + acc_t2), "Horas Netas": fmt_num_es(total_neto_planta), "Horas Descanso": "-", "Hora Fin": float_to_time_str(curr_t)})
            render_aggrid(pd.DataFrame(resumen_final), "grid_resumen_final", height=250)

            for s in sims:
                st.markdown(f"#### 📄 Detalle Línea {s['id']} - Turno {s['turno']}")
                fin_str = float_to_time_str(s['end_time'] if s['end_time'] else s['start_time'] + s['horas_netas'] + s['horas_descanso'])
                st.info(f"Finalizada a las {fin_str} ({s['horas_netas']:.2f}h netas)")
                if not s['resumen_tabla'].empty: render_aggrid(s['resumen_tabla'], f"grid_det_{s['id']}_{s['turno']}", height=200)
