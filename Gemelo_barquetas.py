# -*- coding: utf-8 -*-
import streamlit as st
import time
import pandas as pd
import datetime 
from collections import deque 
import altair as alt 
import math
import json
import os
import copy 

# --- Configuración de la página ---
st.set_page_config(
    page_title="Gemelo Digital: Sala de embarquetado", 
    page_icon="🥩",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={'About': "Gemelo Digital de Sala de Loncheado y Envasado"}
)

# --- Funciones Helper de Tiempo (Sin cambios) ---
def time_to_float(time_obj):
    return time_obj.hour + time_obj.minute / 60.0

def float_to_time_str(time_float):
    hours = int(time_float)
    minutes = round((time_float - hours) * 60)
    if minutes == 60:
        hours += 1
        minutes = 0
    days = hours // 24
    hours = hours % 24
    if days > 0:
        return f"{hours:02d}:{minutes:02d} (Día +{days})"
    else:
        return f"{hours:02d}:{minutes:02d}"

def calcular_descanso_en_tramo(tramo_start_float, tramo_end_float, breaks_config):
    total_descanso = 0.0
    for i in range(1, 4):
        if not breaks_config[f'skip_{i}']:
            break_start_float = breaks_config[f'start_{i}']
            break_end_float = breaks_config[f'end_{i}']
            if break_end_float < break_start_float:
                overlap1 = max(0, min(tramo_end_float, 24.0) - max(tramo_start_float, break_start_float))
                overlap2 = max(0, min(tramo_end_float, break_end_float) - max(tramo_start_float, 0.0))
                total_descanso += overlap1 + overlap2
            else:
                overlap = max(0, min(tramo_end_float, break_end_float) - max(tramo_start_float, break_start_float))
                total_descanso += overlap
    return total_descanso

# --- Funciones de carga (Versión final) ---

def str_to_time(time_str):
    """Convierte un string 'HH:MM' a un objeto time. Devuelve None si es inválido."""
    if not time_str or pd.isna(time_str):
        return None
    try:
        return datetime.datetime.strptime(str(time_str).strip(), '%H:%M').time()
    except ValueError:
        return None

def str_to_bool(bool_str):
    """Convierte un string 'TRUE'/'FALSE' a un booleano."""
    return str(bool_str).strip().upper() == 'TRUE'

def safe_get_int(val, default):
    """
    Convierte de forma segura a int, limpiando comas, puntos y espacios.
    Maneja formatos como "2.000" o "1,500".
    """
    if pd.isna(val):
        return default
    
    s_val = str(val).strip().replace('.', '').replace(',', '')
    
    if s_val == '':
        return default
            
    try:
        return int(float(s_val))
    except (ValueError, TypeError):
        return default

def load_data_from_sheets():
    """Carga toda la configuración desde los enlaces CSV públicos de Google Sheets."""
    
    if "google_sheet_config" not in st.secrets:
        st.error("ERROR: No se han configurado los 'Secrets' en Streamlit Cloud.")
        st.error("Añade [google_sheet_config] con 'config_lineas_url' y 'plan_produccion_url'.")
        st.session_state.id_articulo_global = 0
        return

    try:
        url_config = st.secrets["google_sheet_config"]["config_lineas_url"]
        url_plan = st.secrets["google_sheet_config"]["plan_produccion_url"]
        
        df_config = pd.read_csv(url_config, dtype=str, keep_default_na=False)
        df_plan = pd.read_csv(url_plan, dtype=str, keep_default_na=False)
        
        st.session_state.df_config_raw = df_config
        st.session_state.df_plan_raw = df_plan
        
        st.session_state.id_articulo_global = 0
        
        for index, row in df_config.iterrows():
            linea_id = str(row['linea_id']).strip()
            if not linea_id:
                continue
                
            prefijo = f"l{linea_id}_"
            clientes_key = f"linea_{linea_id}_clientes"
            
            oee_global = safe_get_int(row.get('oee_global'), 85)
            st.session_state[f"{prefijo}oee"] = oee_global
            st.session_state[f"last_global_oee_{prefijo}"] = oee_global
            st.session_state[f"{prefijo}hora_inicio_simulacion"] = str_to_time(row.get('hora_inicio')) or datetime.time(8, 0)
            
            for i in range(1, 4):
                st.session_state[f"{prefijo}descanso_{i}_inicio"] = str_to_time(row.get(f'desc_{i}_inicio')) or datetime.time(10 + i*2, 0)
                st.session_state[f"{prefijo}descanso_{i}_fin"] = str_to_time(row.get(f'desc_{i}_fin')) or datetime.time(10 + i*2, 15)
                st.session_state[f"{prefijo}descanso_{i}_skip"] = str_to_bool(row.get(f'desc_{i}_skip', 'FALSE'))
                
            st.session_state[clientes_key] = []
            st.session_state[f"next_client_id_{linea_id}"] = (st.session_state.id_articulo_global + 1) * 100
            st.session_state[f"next_article_id_{linea_id}"] = (st.session_state.id_articulo_global + 1) * 1000

        if 'hora_entrada_cliente' not in df_plan.columns:
            df_plan['hora_entrada_cliente'] = ""
        if 'act_hora_entrada' not in df_plan.columns:
             df_plan['act_hora_entrada'] = "FALSE"
        if 'act_oee_articulo' not in df_plan.columns:
             df_plan['act_oee_articulo'] = "FALSE"
        if 'oee_articulo' not in df_plan.columns:
             df_plan['oee_articulo'] = ""

        df_plan['hora_validada'] = df_plan['hora_entrada_cliente'].apply(str_to_time)
        df_plan['linea_id'] = df_plan['linea_id'].astype(str).str.strip()
        df_plan['nombre_cliente'] = df_plan['nombre_cliente'].astype(str).str.strip()
        df_plan['nombre_articulo'] = df_plan['nombre_articulo'].astype(str).str.strip()

        horas_por_cliente = df_plan.groupby(['linea_id', 'nombre_cliente'])['hora_validada'].last()
        
        clientes_agrupados = {}
        for index, articulo_row in df_plan.iterrows():
            
            linea_id = articulo_row['linea_id']
            nombre_cliente = articulo_row['nombre_cliente']
            nombre_articulo = articulo_row['nombre_articulo'] 
            
            if not linea_id or not nombre_cliente:
                continue
                
            cliente_key = f"{linea_id}_{nombre_cliente}"
            
            if cliente_key not in clientes_agrupados:
                if f"l{linea_id}_oee" not in st.session_state:
                    st.warning(f"Error en 'Plan_Produccion': La línea '{linea_id}' no existe en 'Config_Lineas'. Omitiendo fila.")
                    continue
                    
                hora_cliente = horas_por_cliente.get((linea_id, nombre_cliente))
                
                clientes_agrupados[cliente_key] = {
                    "id": st.session_state[f"next_client_id_{linea_id}"],
                    "nombre_cliente": nombre_cliente,
                    "articulos": [],
                    "act_hora_entrada": isinstance(hora_cliente, datetime.time),
                    "hora_entrada": hora_cliente if isinstance(hora_cliente, datetime.time) else datetime.time(8, 0)
                }
                st.session_state[f"next_client_id_{linea_id}"] += 1

            if str_to_bool(articulo_row.get('act_hora_entrada', 'FALSE')):
                hora = str_to_time(articulo_row.get('hora_entrada_cliente'))
                if hora:
                    clientes_agrupados[cliente_key]["act_hora_entrada"] = True
                    clientes_agrupados[cliente_key]["hora_entrada"] = hora

            oee_linea = st.session_state.get(f"l{linea_id}_oee", 85)
            oee_final = oee_linea 
            
            if str_to_bool(articulo_row.get('act_oee_articulo', 'FALSE')):
                oee_articulo_val = articulo_row.get('oee_articulo')
                oee_final = safe_get_int(oee_articulo_val, oee_linea)
            
            if nombre_articulo and not pd.isna(nombre_articulo):
                articulo = {
                    "id": st.session_state[f"next_article_id_{linea_id}"],
                    "nombre_articulo": nombre_articulo,
                    "barquetas_pedido": safe_get_int(articulo_row.get('barquetas_pedido'), 0),
                    "velocidad_estimada": safe_get_int(articulo_row.get('velocidad_estimada'), 800),
                    "oee": oee_final
                }
                clientes_agrupados[cliente_key]["articulos"].append(articulo)
                st.session_state[f"next_article_id_{linea_id}"] += 1
                st.session_state.id_articulo_global += 1

        for cliente_key, cliente_obj in clientes_agrupados.items():
            linea_id = cliente_key.split('_')[0]
            st.session_state[f"linea_{linea_id}_clientes"].append(cliente_obj)
            
        st.toast('✅ Configuración cargada desde Google Sheets.', icon="☁️")

    except Exception as e:
        st.error(f"Error fatal al cargar datos de Google Sheets: {e}")
        st.error("Asegúrate de que los 'Secrets' (URLs) son correctos y las pestañas están publicadas como .csv.")
        st.session_state.id_articulo_global = 0


# --- Título ---
st.markdown("<h1 style='text-align: center;'>Gemelo Digital: Sala de embarquetado V.1</h1>", unsafe_allow_html=True)


# --- Inicializar variables de estado de sesión ---
if 'segundos_por_hora_sim' not in st.session_state:
    st.session_state.segundos_por_hora_sim = 0.25 
if 'debug_mode' not in st.session_state:
    st.session_state.debug_mode = False

if 'config_loaded' not in st.session_state:
    load_data_from_sheets()
    st.session_state.config_loaded = True


# --- CSS (sin cambios) ---
st.markdown("""
<style>
/* (CSS Omitido por brevedad) */
:root { --primary-color: #3498db; --secondary-color: #e67e22; --danger-color: #e74c3c; --background-color: #ecf0f1; --card-background-color: #ffffff; --text-color: #2c3e50; --light-gray: #f9f99; --dark-blue: #2980b9; }
body { background-color: var(--background-color); color: var(--text-color); }
div.stApp { background-color: var(--background-color); }
div[data-testid="stAppViewContainer"] { background-color: var(--background-color); }
h1 { color: var(--dark-blue); text-align: center; font-size: 2.8rem; margin-bottom: 1.5rem; }
div[data-testid="stTabs"] h2 { color: var(--text-color); font-size: 2.2rem !important; font-weight: 600; }
div[data-testid="stTabs"] h3 { color: var(--dark-blue); font-size: 2rem !important; font-weight: 600; margin-top: 1.5rem; margin-bottom: 1rem; }
div[data-testid="stVerticalBlock"] { background-color: var(--background-color); padding: 1rem; border-radius: 8px; }
div[data-baseweb="tab-list"] { background-color: var(--light-gray) !important; border-radius: 8px; padding: 0.5rem 0.5rem; margin-bottom: 1rem; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
div[data-baseweb="tab-list"] button { font-size: 2.5rem !important; padding: 1rem 2rem !important; font-weight: 600 !important; }
div[data-baseweb="tab-list"] button[aria-selected="true"] { background-color: var(--primary-color) !important; color: white !important; border-radius: 6px; font-weight: bold; box-shadow: 0 2px 5px rgba(0,0,0,0.2); }
div[data-baseweb="tab-list"] button[aria-selected="false"] { background-color: transparent !important; color: var(--text-color) !important; }
div[data-testid="stTimeInput"] label p,
div[data-testid="stNumberInput"] label p,
div[data-testid="stSlider"] label p,
div[data-testid="stCheckbox"] label p,
div[data-testid="stDateInput"] label p { font-size: 1.3rem !important; font-weight: 600 !important; color: var(--text-color); }
div[data-testid="stTimeInput"] input,
div[data-testid="stNumberInput"] input,
div[data-testid="stDateInput"] input { font-size: 1.3rem !important; padding: 0.5rem 1rem; border-radius: 5px; border: 1px solid #ccc; }
/* Estilo del TÍTULO del Expander para que parezca un H2 */
[data-testid="stExpander"] summary p {
    font-size: 2.2rem !important; /* Aumentado de 1.35rem */
    font-weight: 600 !important;
    color: var(--text-color) !important; /* Cambiado de --dark-blue */
}
div[data-testid="stMetric"] { background-color: var(--card-background-color); padding: 1rem; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); text-align: center; margin-bottom: 1rem; }
div[data-testid="stMetric"] label { font-size: 1.1rem; color: var(--dark-blue); font-weight: bold; }
div[data-testid="stMetricValue"] { font-size: 1.8rem !important; color: var(--primary-color); font-weight: bolder; }
button[kind="primary"] { background-color: var(--primary-color) !important; color: white !important; font-size: 1.5rem !important; padding: 0.8rem 2rem !important; border-radius: 8px !important; border: none !important; transition: background-color 0.3s ease !important; }
button[kind="primary"]:hover { background-color: var(--dark-blue) !important; color: white !important; }
.stDataFrame { text-align: center; }
.stDataFrame th.col_heading { font-size: 2.5rem !important; text-align: center !important; font-weight: bold; color: var(--dark-blue); background-color: var(--light-gray) !important; padding: 12px 8px; }
.stDataFrame th.row_heading { font-size: 2.5rem !important; text-align: center !important; font-weight: bold; color: var(--dark-blue); background-color: var(--light-gray) !important; padding: 12px 8px; }
.stDataFrame td { font-size: 2.2rem !important; text-align: center !important; padding: 10px 8px; vertical-align: middle; }
</style>
""", unsafe_allow_html=True)


# --- Callbacks Genéricos ---
def update_article_oee(linea_id):
    prefijo = f"l{linea_id}_"
    oee_key = f"{prefijo}oee"
    last_oee_key = f"last_global_oee_{prefijo}"
    clientes_key = f"linea_{linea_id}_clientes"
    
    new_global_oee = st.session_state[oee_key]
    old_global_oee = st.session_state[last_oee_key]
    if new_global_oee == old_global_oee: return
    
    for cliente in st.session_state[clientes_key]:
        for articulo in cliente['articulos']:
            articulo_widget_key = f"{prefijo}articulo_oee_{articulo['id']}"
            
            if articulo['oee'] == old_global_oee:
                 articulo['oee'] = new_global_oee
                 if articulo_widget_key in st.session_state:
                     st.session_state[articulo_widget_key] = new_global_oee
                     
    st.session_state[last_oee_key] = new_global_oee

def add_cliente(linea_id):
    clientes_key = f"linea_{linea_id}_clientes"
    next_client_key = f"next_client_id_{linea_id}"
    
    cliente_id = st.session_state[next_client_key]
    st.session_state[next_client_key] += 1
    nuevo_cliente = {
        "id": cliente_id, "nombre_cliente": f"Nuevo Cliente {cliente_id}", "articulos": [],
        "act_hora_entrada": False, "hora_entrada": datetime.time(8, 0)
    }
    st.session_state[clientes_key].append(nuevo_cliente)
    st.warning("Cliente añadido solo en esta sesión. Edita el Google Sheet para guardarlo.")

def remove_cliente(linea_id, cliente_id):
    clientes_key = f"linea_{linea_id}_clientes"
    st.session_state[clientes_key] = [c for c in st.session_state[clientes_key] if c['id'] != cliente_id]
    st.warning("Cliente eliminado solo en esta sesión. Edita el Google Sheet para guardarlo.")


def add_articulo(linea_id, cliente_id):
    prefijo = f"l{linea_id}_"
    oee_key = f"{prefijo}oee"
    clientes_key = f"linea_{linea_id}_clientes"
    next_article_key = f"next_article_id_{linea_id}"
    
    articulo_id = st.session_state[next_article_key]
    st.session_state[next_article_key] += 1
    nuevo_articulo = {
        "id": articulo_id, "nombre_articulo": f"Nuevo Artículo {articulo_id}", 
        "barquetas_pedido": 1000, "velocidad_estimada": 800,
        "oee": st.session_state[oee_key] 
    }
    for cliente in st.session_state[clientes_key]:
        if cliente['id'] == cliente_id:
            cliente['articulos'].append(nuevo_articulo); break
    st.warning("Artículo añadido solo en esta sesión. Edita el Google Sheet para guardarlo.")


def remove_articulo(linea_id, cliente_id, articulo_id):
    clientes_key = f"linea_{linea_id}_clientes"
    for cliente in st.session_state[clientes_key]:
        if cliente['id'] == cliente_id:
            cliente['articulos'] = [a for a in cliente['articulos'] if a['id'] != articulo_id]; break
    st.warning("Artículo eliminado solo en esta sesión. Edita el Google Sheet para guardarlo.")

# --- Pestañas ---
tab_cfg, tab_sim = st.tabs(["⚙️ Configuración", "🚀 Simulación"]) 

# --- PESTAÑA 1: CONFIGURACIÓN ---
with tab_cfg:
    
    # --- Parámetros Globales (¡dentro de la pestaña!) ---
    st.markdown("<h2>Parámetros Globales</h2>", unsafe_allow_html=True)
    st.slider("Velocidad Simulación (s/h)", 0.0, 5.0, 
                value=st.session_state.segundos_por_hora_sim, 
                step=0.05, key="segundos_por_hora_sim")

    st.checkbox("Modo Depuración (Mostrar datos crudos)", key="debug_mode",
                help="Si la app no lee bien tus cambios, activa esto y refresca la página (F5) para ver qué está leyendo de Google Sheets.")
    st.info("La configuración se carga automáticamente desde Google Sheets. Los cambios aquí son temporales.")
    st.markdown("---")
    
    if st.session_state.get('debug_mode', False):
        st.subheader("Datos en Crudo (Config_Lineas)")
        st.dataframe(st.session_state.get('df_config_raw', pd.DataFrame()))
        st.subheader("Datos en Crudo (Plan_Produccion)")
        st.dataframe(st.session_state.get('df_plan_raw', pd.DataFrame()))
            
        st.subheader("Comprobación de Columnas (Plan_Produccion)")
        expected_cols = ['linea_id', 'nombre_cliente', 'act_hora_entrada', 'hora_entrada_cliente', 'nombre_articulo', 'barquetas_pedido', 'velocidad_estimada', 'act_oee_articulo', 'oee_articulo']
        found_cols = list(st.session_state.get('df_plan_raw', pd.DataFrame()).columns)
        st.write("**Columnas esperadas por el script:**")
        st.write(f"`{expected_cols}`")
        st.write("**Columnas encontradas en tu Google Sheet:**")
        st.write(f"`{found_cols}`")
        
        missing_cols = [col for col in expected_cols if col not in found_cols]
        if missing_cols:
            st.error(f"¡ERROR! Faltan las siguientes columnas en tu Google Sheet 'Plan_Produccion': `{missing_cols}`")
            st.error("Por favor, renombra las columnas en tu hoja para que coincidan EXACTAMENTE.")
        else:
            st.success("¡Coinciden todas las columnas! El problema debe estar en los datos (ej. 'FALSO' en lugar de 'FALSE').")

    def dibujar_config_linea(linea_id):
        prefijo = f"l{linea_id}_"
        clientes_key = f"linea_{linea_id}_clientes"
        oee_key = f"{prefijo}oee"
        
        if oee_key not in st.session_state:
            st.warning(f"La línea {linea_id} no se encontró en la hoja 'Config_Lineas' de Google Sheets.")
            return
        
        with st.expander(f"🥩 Configuración Línea {linea_id}", expanded=(linea_id=='5')):
            
            # --- *** MODIFICACIÓN: Añadido value=True *** ---
            st.checkbox(
                f"Incluir Línea {linea_id} en la Simulación", 
                value=True, # <-- AÑADIDO
                key=f"{prefijo}linea_activa",
                help=f"Si se desmarca, la Línea {linea_id} será completamente ignorada en la Simulación Global."
            )
            # --- *** FIN DE LA MODIFICACIÓN *** ---
            
            with st.expander(f"Ver/Ocultar Parámetros de Horarios y Descansos (Línea {linea_id})", expanded=False):
                st.markdown("<h3>🕒 Horario</h3>", unsafe_allow_html=True)
                st.time_input(f"Hora Inicio Producción (Línea {linea_id})", 
                              key=f"{prefijo}hora_inicio_simulacion", 
                              step=datetime.timedelta(minutes=15),
                              help=f"La hora (hh:mm) a la que comienza la simulación la Línea {linea_id}.")

                st.markdown(f"<h3>⏱️ Parámetros de Descansos (Línea {linea_id})</h3>", unsafe_allow_html=True)
                col_d1, col_d2, col_d3 = st.columns(3)
                for i in range(1, 4):
                    with locals()[f"col_d{i}"]:
                        st.markdown(f"<h5>Descanso {i}</h5>", unsafe_allow_html=True)
                        st.time_input(f"Inicio Descanso {i}", key=f"{prefijo}descanso_{i}_inicio", step=datetime.timedelta(minutes=15))
                        st.time_input(f"Fin Descanso {i}", key=f"{prefijo}descanso_{i}_fin", step=datetime.timedelta(minutes=15))
                        st.checkbox(f"No hay descanso {i}", key=f"{prefijo}descanso_{i}_skip")

            st.markdown("---")
            st.markdown(f"<h3>🏭 OEE y Plan de Producción (Línea {linea_id})</h3>", unsafe_allow_html=True)
            st.slider(f"OEE (%) Línea {linea_id} (Global)", 1, 100, 
                     key=oee_key, 
                     help=f"OEE global para la Línea {linea_id}. Cambiar esto actualizará todos sus artículos que no hayan sido modificados manualmente.",
                     on_change=update_article_oee, args=(linea_id,))
            st.info(f"Nota: Al cambiar el OEE Global de L{linea_id}, solo se actualizarán sus artículos que no hayan sido modificados manualmente.")
                     
            st.markdown("---")
            st.markdown(f"<h4>Plan de Producción (Línea {linea_id})</h4>", unsafe_allow_html=True)
            
            clientes_a_dibujar = st.session_state.get(clientes_key, [])
            for i, cliente in enumerate(clientes_a_dibujar):
                try: 
                    exp_title = st.session_state[clientes_key][i]['nombre_cliente']
                except (IndexError, KeyError): 
                    continue 
                
                with st.expander(f"**Cliente: {exp_title}** (ID: {cliente['id']})", expanded=True):
                    col_cn, col_cb = st.columns([3, 1])
                    with col_cn:
                        st.session_state[clientes_key][i]['nombre_cliente'] = st.text_input("Nombre del Cliente", value=cliente['nombre_cliente'], key=f"{prefijo}cliente_nombre_{cliente['id']}")
                    with col_cb:
                        st.write("Eliminar Cliente:"); st.button("🗑️", key=f"{prefijo}remove_cliente_{cliente['id']}", on_click=remove_cliente, args=(linea_id, cliente['id']))
                    
                    st.markdown("---")
                    col_h1, col_h2 = st.columns(2)
                    with col_h1:
                        st.session_state[clientes_key][i]['act_hora_entrada'] = st.checkbox("Activar Hora Entrada Pedido", 
                                                                                            value=cliente.get('act_hora_entrada', False), 
                                                                                            key=f"{prefijo}cliente_act_hora_{cliente['id']}",
                                                                                            help="Si se activa, los artículos de este cliente no empezarán a producirse hasta la hora indicada.")
                    with col_h2:
                        st.session_state[clientes_key][i]['hora_entrada'] = st.time_input("Hora Entrada Pedido", 
                                                                                           value=cliente.get('hora_entrada', datetime.time(8,0)), 
                                                                                           key=f"{prefijo}cliente_hora_{cliente['id']}", 
                                                                                           step=datetime.timedelta(minutes=15),
                                                                                           disabled=not st.session_state[clientes_key][i]['act_hora_entrada'])
                    st.markdown("---")
                    
                    st.markdown("##### Artículos de este cliente:")
                    c_a, c_b, c_c, c_d, c_e = st.columns([3, 2, 2, 2, 1])
                    c_a.markdown("**Artículo**"); c_b.markdown("**Barquetas Pedidas**"); c_c.markdown("**Velocidad (u/h)**"); c_d.markdown("**OEE (%)**"); c_e.markdown("**Quitar**")
                    
                    for j, articulo in enumerate(cliente['articulos']):
                        col1, col2, col3, col4, col5 = st.columns([3, 2, 2, 2, 1])
                        try:
                            with col1:
                                st.session_state[clientes_key][i]['articulos'][j]['nombre_articulo'] = st.text_input("Nombre Artículo", value=articulo['nombre_articulo'], key=f"{prefijo}articulo_nombre_{articulo['id']}", label_visibility="collapsed")
                            with col2:
                                st.session_state[clientes_key][i]['articulos'][j]['barquetas_pedido'] = st.number_input("Barquetas", value=articulo['barquetas_pedido'], min_value=0, key=f"{prefijo}articulo_barquetas_{articulo['id']}", label_visibility="collapsed")
                            with col3:
                                st.session_state[clientes_key][i]['articulos'][j]['velocidad_estimada'] = st.number_input("Velocidad", value=articulo['velocidad_estimada'], min_value=1, key=f"{prefijo}articulo_velocidad_{articulo['id']}", label_visibility="collapsed")
                            
                            with col4:
                                widget_key = f"{prefijo}articulo_oee_{articulo['id']}"
                                data_value = articulo['oee']
                                if widget_key not in st.session_state:
                                    st.session_state[widget_key] = data_value
                                oee_del_widget = st.number_input("OEE %", 
                                                         min_value=1, max_value=100, 
                                                         key=widget_key, 
                                                         label_visibility="collapsed")
                                st.session_state[clientes_key][i]['articulos'][j]['oee'] = oee_del_widget
                            
                            with col5:
                                st.button("➖", key=f"{prefijo}remove_articulo_{articulo['id']}", on_click=remove_articulo, args=(linea_id, cliente['id'], articulo['id']))
                        except (IndexError, KeyError): continue
                    st.button("➕ Añadir Artículo", key=f"{prefijo}add_articulo_{cliente['id']}", on_click=add_articulo, args=(linea_id, cliente['id']))
            st.markdown("---")
            st.button(f"👤➕ Añadir Cliente a Línea {linea_id}", on_click=add_cliente, args=(linea_id,), type="primary")

    # Dibujar todas las líneas
    dibujar_config_linea('5')
    dibujar_config_linea('4')
    dibujar_config_linea('3')
    dibujar_config_linea('1')

# --- PESTAÑA 2: SIMULACIÓN ---
def setup_simulation_state(linea_id):
    prefijo = f"l{linea_id}_"
    
    if f"{prefijo}linea_activa" not in st.session_state:
        return {"active": False} 
        
    if not st.session_state.get(f"{prefijo}linea_activa", False):
        return {
            "linea_id": linea_id, "queue": deque(), "current_article": None,
            "resumen_articulos": [], "breaks_config": {},
            "start_time": 0.0, "total_barquetas_objetivo": 0,
            "total_barquetas_producidas": 0.0, "total_horas_trabajo_neto": 0.0,
            "total_horas_descanso": 0.0, "historial_produccion": [],
            "active": False
        }
    
    clientes_key = f"linea_{linea_id}_clientes"
    
    plan_produccion = deque()
    total_barquetas_objetivo = 0
    total_horas_estimadas_neto = 0.0
    resumen_articulos = []
    
    clientes_a_procesar = st.session_state.get(clientes_key, [])
    for cliente in clientes_a_procesar:
        
        hora_entrada_pedido_float = 0.0
        if cliente.get('act_hora_entrada', False):
            hora_entrada_pedido_float = time_to_float(cliente.get('hora_entrada', datetime.time(0, 0)))
            
        for articulo in cliente['articulos']:
            if articulo['barquetas_pedido'] > 0:
                
                articulo_oee = articulo['oee'] 
                oee_factor = articulo_oee / 100.0
                velocidad_real = articulo['velocidad_estimada'] * oee_factor
                horas_necesarias = (articulo['barquetas_pedido'] / velocidad_real) if velocidad_real > 0 else 0
                
                plan_produccion.append({
                    "cliente": cliente['nombre_cliente'], "articulo": articulo['nombre_articulo'],
                    "barquetas_pedido": articulo['barquetas_pedido'], 
                    "velocidad_real": velocidad_real, "barquetas_pendientes": articulo['barquetas_pedido'],
                    "hora_entrada_pedido_float": hora_entrada_pedido_float
                })
                resumen_articulos.append({
                    "Cliente": cliente['nombre_cliente'], "Artículo": articulo['nombre_articulo'],
                    "Barquetas Pedidas": articulo['barquetas_pedido'], "Vel. Estimada (u/h)": articulo['velocidad_estimada'],
                    "OEE Aplicado (%)": articulo_oee, "Vel. Real (OEE) (u/h)": velocidad_real, 
                    "Horas Estimadas": horas_necesarias
                })
                total_barquetas_objetivo += articulo['barquetas_pedido']
                total_horas_estimadas_neto += horas_necesarias

    breaks_config = {}
    for i in range(1, 4):
        breaks_config[f'start_{i}'] = time_to_float(st.session_state[f"{prefijo}descanso_{i}_inicio"])
        breaks_config[f'end_{i}'] = time_to_float(st.session_state[f"{prefijo}descanso_{i}_fin"])
        breaks_config[f'skip_{i}'] = st.session_state[f"{prefijo}descanso_{i}_skip"]

    return {
        "linea_id": linea_id, "queue": plan_produccion, "current_article": None,
        "resumen_articulos": resumen_articulos, "breaks_config": breaks_config,
        "start_time": time_to_float(st.session_state[f"{prefijo}hora_inicio_simulacion"]),
        "total_barquetas_objetivo": total_barquetas_objetivo,
        "total_barquetas_producidas": 0.0, "total_horas_trabajo_neto": 0.0,
        "total_horas_descanso": 0.0, "historial_produccion": [],
        "active": total_barquetas_objetivo > 0
    }

def process_line_tick(sim_state, current_time_float, target_time_float):
    if not sim_state.get('active', False): 
        return 0.0, 0.0, 0.0 

    tramo_start_linea = max(sim_state['start_time'], current_time_float)
    tramo_end = target_time_float
    
    if tramo_start_linea >= tramo_end: 
        return 0.0, 0.0, 0.0

    tramo_start_articulo = 0.0
    if not sim_state['current_article'] and sim_state['queue']:
        next_articulo = sim_state['queue'][0]
        tramo_start_articulo = next_articulo.get('hora_entrada_pedido_float', 0.0)
    
    tramo_start_trabajo_efectivo = max(tramo_start_linea, tramo_start_articulo)
    
    if tramo_start_trabajo_efectivo >= tramo_end:
        return 0.0, 0.0, 0.0 
        
    horas_en_este_ciclo_bruto_trabajo = tramo_end - tramo_start_trabajo_efectivo
    
    current_time_day_float = tramo_start_trabajo_efectivo % 24
    target_time_day_float = tramo_end % 24
    if target_time_day_float == 0: target_time_day_float = 24.0
    
    horas_descanso_en_ciclo = calcular_descanso_en_tramo(current_time_day_float, target_time_day_float, sim_state['breaks_config'])
    horas_trabajo_en_ciclo_NETO = max(0, horas_en_este_ciclo_bruto_trabajo - horas_descanso_en_ciclo)
    
    produccion_total_este_ciclo = 0.0
    horas_trabajadas_este_ciclo = 0.0

    if horas_trabajo_en_ciclo_NETO > 0.001:
        while horas_trabajadas_este_ciclo < horas_trabajo_en_ciclo_NETO:
            if not sim_state['current_article']:
                if sim_state['queue']:
                    next_articulo = sim_state['queue'][0]
                    if next_articulo.get('hora_entrada_pedido_float', 0.0) <= tramo_start_trabajo_efectivo:
                        sim_state['current_article'] = sim_state['queue'].popleft()
                    else:
                        break
                else:
                    sim_state['active'] = False
                    break 
            
            articulo = sim_state['current_article']
            velocidad_real = articulo['velocidad_real']
            if velocidad_real == 0: break 
            
            horas_necesarias_articulo = articulo['barquetas_pendientes'] / velocidad_real
            horas_trabajo_disponible = horas_trabajo_en_ciclo_NETO - horas_trabajadas_este_ciclo
            horas_a_usar = min(horas_necesarias_articulo, horas_trabajo_disponible)
            
            barquetas_producidas_ahora = horas_a_usar * velocidad_real
            
            articulo['barquetas_pendientes'] -= barquetas_producidas_ahora
            sim_state['total_barquetas_producidas'] += barquetas_producidas_ahora
            produccion_total_este_ciclo += barquetas_producidas_ahora
            
            horas_trabajadas_este_ciclo += horas_a_usar
            sim_state['total_horas_trabajo_neto'] += horas_a_usar
            
            if articulo['barquetas_pendientes'] < 0.01:
                sim_state['current_article'] = None
            if not sim_state['current_article'] and not sim_state['queue']:
                sim_state['active'] = False
                break
    
    sim_state['total_horas_descanso'] += horas_descanso_en_ciclo
    
    sim_state['historial_produccion'].append({
        'Hora': int(target_time_float),
        'Producción Hora': produccion_total_este_ciclo,
        'Producción Acumulada': sim_state['total_barquetas_producidas']
    })
    
    return produccion_total_este_ciclo, horas_trabajadas_este_ciclo, horas_descanso_en_ciclo

with tab_sim:
    st.markdown("<h3>🔴 Simulación Global</h3>", unsafe_allow_html=True)
    placeholder_metricas_globales = st.empty()
    placeholder_grafico_global = st.empty() 
    placeholder_metricas_lineas = st.empty()
    placeholder_graficos_lineas = st.empty()
    placeholder_progreso = st.empty()
    placeholder_tablas_finales = st.empty()

    if st.button("Iniciar Simulación Global", key="start_sim_button_global", type="primary"):
        sim_l5 = setup_simulation_state('5')
        sim_l4 = setup_simulation_state('4')
        sim_l3 = setup_simulation_state('3')
        sim_l1 = setup_simulation_state('1')
        sims = [s for s in [sim_l5, sim_l4, sim_l3, sim_l1] if s.get('active', False) is not None]
        
        active_sims = [s for s in sims if s['active']]
        
        if not active_sims:
            st.error("No hay barquetas pedidas en ninguna línea *activa* cargada desde Google Sheets. Simulación no iniciada.")
        else:
            global_start_time_sim = min(s['start_time'] for s in active_sims)
            current_time_float = global_start_time_sim
            
            historial_global = []
            total_barquetas_global = 0.0
            total_neto_global = 0.0
            total_descanso_global = 0.0
            
            while any(s['active'] for s in sims):
                if abs(current_time_float % 1.0) < 0.001:
                    target_time_float = current_time_float + 1.0
                else:
                    target_time_float = math.floor(current_time_float) + 1.0
                
                prod_l5, neto_l5, descanso_l5 = process_line_tick(sim_l5, current_time_float, target_time_float)
                prod_l4, neto_l4, descanso_l4 = process_line_tick(sim_l4, current_time_float, target_time_float)
                prod_l3, neto_l3, descanso_l3 = process_line_tick(sim_l3, current_time_float, target_time_float)
                prod_l1, neto_l1, descanso_l1 = process_line_tick(sim_l1, current_time_float, target_time_float)
                
                prod_global_tick = prod_l5 + prod_l4 + prod_l3 + prod_l1
                total_barquetas_global += prod_global_tick
                total_neto_global += neto_l5 + neto_l4 + neto_l3 + neto_l1
                total_descanso_global += descanso_l5 + descanso_l4 + descanso_l3 + descanso_l1
                
                historial_global.append({
                    'Hora': int(target_time_float),
                    'Producción Hora': prod_global_tick,
                    'Producción Acumulada': total_barquetas_global
                })
                df_historial_global = pd.DataFrame(historial_global)

                with placeholder_metricas_globales.container():
                    horas_transcurridas_global = target_time_float - global_start_time_sim

                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("PRODUCIDAS (GLOBAL)", f"{total_barquetas_global:,.0f}".replace(',', '.'))
                    col2.metric("HORA (RELOJ)", float_to_time_str(target_time_float)) 
                    col3.metric("HORAS TRANSCURRIDAS (GLOBAL)", f"{horas_transcurridas_global:,.2f} h")
                    col4.metric("HORAS DESCANSO (GLOBAL)", f"{total_descanso_global:,.2f} h")

                with placeholder_grafico_global.container():
                    if not df_historial_global.empty:
                        base = alt.Chart(df_historial_global).encode(
                            x=alt.X('Hora:O', sort='ascending', axis=alt.Axis(title='Hora del Día', labelFontSize=14, titleFontSize=16))
                        )
                        # --- *** CORRECCIÓN GRÁFICO 1: Añadir orient='left' *** ---
                        barras = base.mark_bar(color='var(--primary-color)').encode(
                            y=alt.Y('Producción Hora:Q', axis=alt.Axis(title='Barquetas por Hora', titleColor='var(--primary-color)', labelFontSize=12, titleFontSize=14, orient='left')),
                            tooltip=[alt.Tooltip('Hora'), alt.Tooltip('Producción Hora', format=',.0f')]
                        ).interactive()
                        
                        texto_barras = base.mark_text(
                            dy=-5, color='black', fontSize=14
                        ).encode(
                            y=alt.Y('Producción Hora:Q', axis=None), # Eje ya definido
                            text=alt.Text('Producción Hora:Q', format=',.0f')
                        )
                        
                        # --- *** CORRECCIÓN GRÁFICO 2: Añadir orient='right' *** ---
                        linea = base.mark_line(color='var(--secondary-color)', point=True).encode(
                            y=alt.Y('Producción Acumulada:Q', axis=alt.Axis(title='Barquetas Acumuladas', titleColor='var(--secondary-color)', labelFontSize=12, titleFontSize=14, orient='right')),
                            tooltip=[alt.Tooltip('Hora'), alt.Tooltip('Producción Acumulada', format=',.0f')]
                        ).interactive()
                        
                        texto_linea = base.mark_text(
                            dy=-10, color='black', fontSize=14
                        ).encode(
                            y=alt.Y('Producción Acumulada:Q', axis=None), # Eje ya definido
                            text=alt.Text('Producción Acumulada:Q', format=',.0f')
                        )
                        
                        chart = alt.layer(barras, texto_barras, linea, texto_linea).resolve_scale(y='independent').properties(
                            title='Producción vs Tiempo (GLOBAL)', height=400
                        )
                        
                        st.altair_chart(chart, use_container_width=True)
                
                with placeholder_metricas_lineas.container():
                    st.markdown("---")
                    st.markdown("<h3>Métricas por Línea</h3>", unsafe_allow_html=True)
                    col5, col4, col3, col1 = st.columns(4)
                    
                    def show_metrics(col, sim_state):
                        if not sim_state.get('linea_id'): return
                        with col:
                            st.markdown(f"<h4 style='text-align: center; color: var(--dark-blue);'>Línea {sim_state['linea_id']}</h4>", unsafe_allow_html=True)
                            
                            if not st.session_state[f"l{sim_state['linea_id']}_linea_activa"]:
                                st.info("Línea Excluida.")
                                st.metric("Producidas (Línea)", "N/A")
                                st.metric("Pendientes (Línea)", "N/A")
                                st.metric("Horas Netas", "N/A")
                                st.metric("Horas Descanso", "N/A")
                                return

                            prod = sim_state['total_barquetas_producidas']
                            pend = sim_state['total_barquetas_objetivo'] - prod
                            neto = sim_state['total_horas_trabajo_neto']
                            desc = sim_state['total_horas_descanso']
                            st.metric("Producidas (Línea)", f"{prod:,.0f}")
                            st.metric("Pendientes (Línea)", f"{pend:,.0f}")
                            st.metric("Horas Netas", f"{neto:,.2f} h")
                            st.metric("Horas Descanso", f"{desc:,.2f} h")
                    
                    show_metrics(col5, sim_l5)
                    show_metrics(col4, sim_l4)
                    show_metrics(col3, sim_l3)
                    show_metrics(col1, sim_l1)

                with placeholder_graficos_lineas.container():
                    st.markdown("---")
                    st.markdown("<h3>Gráficos por Línea</h3>", unsafe_allow_html=True)
                    
                    def show_graph(sim_state):
                        if not sim_state.get('linea_id'): return
                        
                        if not st.session_state[f"l{sim_state['linea_id']}_linea_activa"]:
                            st.info(f"Línea {sim_state['linea_id']} excluida de la simulación.")
                            return
                        
                        if not sim_state['historial_produccion']:
                            if sim_state['total_barquetas_objetivo'] == 0:
                                st.info(f"Línea {sim_state['linea_id']} sin pedidos.")
                            else:
                                st.info(f"Línea {sim_state['linea_id']} sin producción.")
                            return
                            
                        df_hist = pd.DataFrame(sim_state['historial_produccion'])
                        base = alt.Chart(df_hist).encode(
                            x=alt.X('Hora:O', sort='ascending', axis=alt.Axis(title='Hora del Día', labelFontSize=10))
                        )
                        # --- *** CORRECCIÓN GRÁFICO 3: Añadir orient='left' *** ---
                        barras = base.mark_bar(color='var(--primary-color)').encode(
                            y=alt.Y('Producción Hora:Q', axis=alt.Axis(title='Barquetas/h', labelFontSize=10, orient='left')),
                            tooltip=[alt.Tooltip('Hora'), alt.Tooltip('Producción Hora', format=',.0f')]
                        )
                        
                        texto_barras_ind = base.mark_text(
                            dy=-5, color='black', fontSize=12
                        ).encode(
                            y=alt.Y('Producción Hora:Q', axis=None), # Eje ya definido
                            text=alt.Text('Producción Hora:Q', format=',.0f')
                        )
                        
                        # --- *** CORRECCIÓN GRÁFICO 4: Añadir orient='right' *** ---
                        linea = base.mark_line(color='var(--secondary-color)', point=True).encode(
                            y=alt.Y('Producción Acumulada:Q', axis=alt.Axis(title='Acumulado', labelFontSize=10, orient='right')),
                            tooltip=[alt.Tooltip('Hora'), alt.Tooltip('Producción Acumulada', format=',.0f')]
                        )
                        
                        texto_linea_ind = base.mark_text(
                            dy=-10, color='black', fontSize=12
                        ).encode(
                            y=alt.Y('Producción Acumulada:Q', axis=None), # Eje ya definido
                            text=alt.Text('Producción Acumulada:Q', format=',.0f')
                        )
                        
                        chart = alt.layer(barras, texto_barras_ind, linea, texto_linea_ind).resolve_scale(y='independent').properties(
                            title=f'Producción Línea {sim_state["linea_id"]}', height=300
                        )
                        
                        st.altair_chart(chart, use_container_width=True)
                    
                    show_graph(sim_l5)
                    st.markdown("---")
                    show_graph(sim_l4)
                    st.markdown("---")
                    show_graph(sim_l3)
                    st.markdown("---")
                    show_graph(sim_l1)

                with placeholder_progreso.container():
                    st.markdown("---")
                    st.markdown("<h3>Progreso por Línea</h3>", unsafe_allow_html=True)
                    
                    def show_progress(sim_state):
                        if not sim_state.get('linea_id'): return
                        
                        if not st.session_state[f"l{sim_state['linea_id']}_linea_activa"]:
                            st.warning(f"**Línea {sim_state['linea_id']}:** Excluida de la simulación.")
                            return
                        
                        if sim_state['total_barquetas_objetivo'] > 0:
                            progreso_percent = (sim_state['total_barquetas_producidas'] / sim_state['total_barquetas_objetivo']) if sim_state['total_barquetas_objetivo'] > 0 else 0
                            st.write(f"**Progreso Línea {sim_state['linea_id']}:** {sim_state['total_barquetas_producidas']:,.0f} / {sim_state['total_barquetas_objetivo']:,.0f} barquetas")
                            st.progress(int(progreso_percent * 100))
                            if sim_state['current_article']:
                                st.write(f"Produciendo: {sim_state['current_article']['articulo']}")
                        else:
                            st.write(f"**Línea {sim_state['linea_id']}:** Sin pedidos.")
                    
                    show_progress(sim_l5)
                    show_progress(sim_l4)
                    show_progress(sim_l3)
                    show_progress(sim_l1)
                
                time.sleep(st.session_state.segundos_por_hora_sim)
                current_time_float = target_time_float
                
                if total_neto_global > 999: 
                    st.error("Simulación detenida: Límite de 999 horas netas globales alcanzado.")
                    break
            
            st.success(f"✅ ¡Simulación Global Completada!")
            st.balloons()
            
            with placeholder_tablas_finales.container():
                st.markdown("---")
                
                st.markdown("<h3>📈 Resumen Global de la Simulación</h3>", unsafe_allow_html=True)
                global_summary_data = []
                all_end_times = []
                all_start_times = []

                for s in sims:
                    if st.session_state[f"l{s['linea_id']}_linea_activa"] and s['total_barquetas_objetivo'] > 0:
                        hora_inicio_obj = st.session_state[f"l{s['linea_id']}_hora_inicio_simulacion"]
                        hora_inicio_float = time_to_float(hora_inicio_obj)
                        hora_fin_float = hora_inicio_float + s['total_horas_trabajo_neto'] + s['total_horas_descanso']
                        
                        all_end_times.append(hora_fin_float)
                        all_start_times.append(hora_inicio_float)
                        
                        global_summary_data.append({
                            "Línea": f"Línea {s['linea_id']}",
                            "Barquetas Totales": s['total_barquetas_producidas'],
                            "Horas Netas": s['total_horas_trabajo_neto'],
                            "Horas Descanso": s['total_horas_descanso'],
                            "Hora Inicio": float_to_time_str(hora_inicio_float),
                            "Hora Fin": float_to_time_str(hora_fin_float)
                        })

                if global_summary_data: 
                    global_start_time = min(all_start_times)
                    global_end_time = max(all_end_times)
                    
                    global_summary_data.append({
                        "Línea": "GLOBAL",
                        "Barquetas Totales": total_barquetas_global,
                        "Horas Netas": global_end_time - global_start_time,
                        "Horas Descanso": total_descanso_global,
                        "Hora Inicio": float_to_time_str(global_start_time),
                        "Hora Fin": float_to_time_str(global_end_time)
                    })

                    df_global_summary = pd.DataFrame(global_summary_data).set_index("Línea")
                    
                    df_global_styled = df_global_summary.style.format({"Barquetas Totales": "{:,.0f}", "Horas Netas": "{:,.2f} h", "Horas Descanso": "{:,.2f} h"}).set_properties(**{'font-size': '1.8rem', 'text_align': 'center'})
                    
                    st.dataframe(df_global_styled)
                
                for sim_state in sims:
                    if st.session_state[f"l{sim_state['linea_id']}_linea_activa"] and sim_state['total_barquetas_objetivo'] > 0:
                        st.markdown(f"<h3>📈 Resumen Tiempos (Línea {sim_state['linea_id']})</h3>", unsafe_allow_html=True)
                        
                        hora_inicio_obj = st.session_state[f"l{sim_state['linea_id']}_hora_inicio_simulacion"]
                        hora_inicio_float = time_to_float(hora_inicio_obj)
                        hora_fin_float = hora_inicio_float + sim_state['total_horas_trabajo_neto'] + sim_state['total_horas_descanso']
                        hora_fin_str = float_to_time_str(hora_fin_float)
                        
                        st.info(f"Línea {sim_state['linea_id']} finalizada a las {hora_fin_str} ({sim_state['total_horas_trabajo_neto']:,.2f}h netas + {sim_state['total_horas_descanso']:,.2f}h descanso)")
                        
                        df_resumen = pd.DataFrame(sim_state['resumen_articulos'])
                        columnas_ordenadas = ["Cliente", "Artículo", "Barquetas Pedidas", "Vel. Estimada (u/h)", "OEE Aplicado (%)", "Vel. Real (OEE) (u/h)", "Horas Estimadas"]
                        df_resumen = df_resumen[columnas_ordenadas]
                        
                        df_styled = df_resumen.style.format({"Barquetas Pedidas": "{:,.0f}", "Vel. Estimada (u/h)": "{:,.0f}", "OEE Aplicado (%)": "{:,.0f} %", "Vel. Real (OEE) (u/h)": "{:,.1f}", "Horas Estimadas": "{:,.2f} h"}).set_properties(**{'font-size': '1.8rem', 'text_align': 'center'})
                        
                        st.dataframe(df_styled)