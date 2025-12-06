import pandas as pd
import numpy as np
import re
import io
import os
import psycopg2
from urllib.parse import urlparse
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from sklearn.cluster import KMeans
from werkzeug.security import safe_str_cmp

# --- CONFIGURACIÓN Y CONEXIÓN A LA BASE DE DATOS ---

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'una_clave_secreta_muy_fuerte_aqui') # Cambiar por una clave más segura

# Usar la URI de tu cuaderno de railway.ipynb
DB_URI = 'postgresql://postgres:KAGJhRklTcsevGqKEgCNPfmdDiGzsLyQ@switchyard.proxy.rlwy.net:13155/railway' #

# Función para obtener la conexión a la DB
def get_db_connection():
    try:
        result = urlparse(DB_URI)
        conn = psycopg2.connect(
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port,
            dbname=result.path.lstrip('/')
        )
        return conn
    except Exception as e:
        print(f"Error al conectar a la base de datos: {e}")
        return None

# Función para crear la tabla de delitos si no existe
def create_delitos_table():
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            # Se usa 'delitos' como nombre de tabla más apropiado para los datos
            # que contienen los registros de crímenes, aunque el usuario mencionó 'fecha'.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS delitos (
                    id_fecha SERIAL PRIMARY KEY,
                    id_municipio INT NOT NULL,
                    fecha_registro DATE NOT NULL,
                    robos INT NOT NULL,
                    secuestros INT NOT NULL,
                    grado VARCHAR(50) NOT EXISTS
                );
            """)
            conn.commit()
            cur.close()
            print("Tabla 'delitos' verificada/creada exitosamente.")
        except Exception as e:
            print(f"Error al crear la tabla 'delitos': {e}")
        finally:
            conn.close()

# Llamar a la función al inicio para asegurar la tabla
create_delitos_table()

# --- AUTENTICACIÓN ---

ADMIN_USER = "admMigrantes"
ADMIN_PASS = "contraseña123"

# Ruta de inicio (Login)
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if safe_str_cmp(username, ADMIN_USER) and safe_str_cmp(password, ADMIN_PASS):
            session['logged_in'] = True
            flash('Inicio de sesión exitoso.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Credenciales incorrectas. Intente de nuevo.', 'danger')

    return render_template('login.html')

# Ruta de cierre de sesión
@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Has cerrado sesión.', 'info')
    return redirect(url_for('login'))

# --- RUTA PROTEGIDA (DASHBOARD) ---

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    ongs_data = []
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            # Seleccionar todos los datos de la tabla 'ongs'
            cur.execute("SELECT * FROM ongs;") #
            ongs_data = cur.fetchall()
            conn.commit()
            cur.close()
        except Exception as e:
            flash(f"Error al cargar datos de ONGs: {e}", 'danger')
        finally:
            conn.close()

    # Obtener nombres de columnas (si es posible)
    ongs_cols = [desc[0] for desc in cur.description] if 'cur' in locals() and cur.description else ["Columna 1", "Columna 2", "..."]

    return render_template('dashboard.html', ongs_data=ongs_data, ongs_cols=ongs_cols)


# --- LÓGICA DE PROCESAMIENTO Y CARGA DE DATOS ---

# Lógica del cuaderno de procesamiento (admin/Preprocesamieto (1) - copia.ipynb)
def process_data_for_db(file_stream):
    try:
        # Cargar datos con codificación latin1 para manejar caracteres especiales
        df = pd.read_csv(file_stream, encoding='latin1')
    except UnicodeDecodeError:
        df = pd.read_csv(file_stream, encoding='utf8') # Intento con utf8 si falla

    # Pasos de pre-procesamiento idénticos al cuaderno:

    # 1. Filtrar por el año máximo (2025)
    max_anio = df['Año'].max()
    df = df[df['Año'] == max_anio].copy()

    # Lista de meses para identificar los meses a conservar
    meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
             'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']

    # Identificar meses válidos y conservar los últimos 3 con data
    meses_validos = [mes for mes in meses
                     if mes in df.columns
                     and df[mes].astype(float).sum() > 0] # Asegurar que tengan datos (suma > 0)
    ultimos_3_meses = meses_validos[-3:] if len(meses_validos) >= 3 else meses_validos
    
    # Eliminar columnas de meses no deseados
    columnas_a_eliminar = [mes for mes in meses if mes in df.columns and mes not in ultimos_3_meses]
    df = df.drop(columns=columnas_a_eliminar)

    # 2. Filtrar por Tipo de delito: Secuestro y Robo
    df = df[df['Tipo de delito'].isin(['Secuestro', 'Robo'])].copy()

    # 3. Eliminar subtipos de delito específicos
    subtipos_a_eliminar = [
        'Robo a transportista', 'Robo a institución bancaria',
        'Robo a negocio', 'Robo de ganado'
    ]
    df = df[~df['Subtipo de delito'].isin(subtipos_a_eliminar)].copy()

    # 4. Filtrar por Modalidades específicas
    modalidades_validas = [
        'Secuestro extorsivo', 'Secuestro con calidad de rehén',
        'Secuestro para causar daño', 'Secuestro exprés', 'Otro tipo de secuestros',
        'Con violencia', 'Sin violencia',
        'Robo de coche de 4 ruedas Con violencia', 'Robo de coche de 4 ruedas Sin violencia',
        'Robo de motocicleta Con violencia', 'Robo de motocicleta Sin violencia'
    ]
    df = df[df['Modalidad'].isin(modalidades_validas)].copy()

    # 5. Seleccionar y agrupar datos por municipio y delito
    columnas_conservar = ['Entidad', 'Cve. Municipio', 'Municipio', 'Tipo de delito'] + ultimos_3_meses
    df = df[columnas_conservar]
    columnas_agrupacion = ['Entidad', 'Cve. Municipio', 'Municipio', 'Tipo de delito']
    df_agrupado = df.groupby(columnas_agrupacion, dropna=False).sum().reset_index()

    # 6. Melt: pasar meses a formato largo
    df_long = df_agrupado.melt(
        id_vars=["Entidad", "Cve. Municipio", "Municipio", "Tipo de delito"],
        value_vars=ultimos_3_meses,
        var_name="Mes",
        value_name="Cantidad"
    )

    # Diccionario meses
    meses_num = {
        "Enero": "01","Febrero": "02","Marzo": "03","Abril": "04",
        "Mayo": "05","Junio": "06","Julio": "07","Agosto": "08",
        "Septiembre": "09","Octubre": "10","Noviembre": "11","Diciembre": "12"
    }

    # Crear columna fecha
    anio = max_anio
    df_long["fecha"] = pd.to_datetime(
        df_long["Mes"].map(meses_num).radd(f"{anio}-") + "-01"
    )

    # 7. Pivot: Robo y Secuestro como columnas
    df_final = df_long.pivot_table(
        index=["Entidad", "Cve. Municipio", "Municipio", "fecha"],
        columns="Tipo de delito",
        values="Cantidad",
        fill_value=0
    ).reset_index().rename(columns={"Robo": "robos", "Secuestro": "secuestros"}) # Renombrar aquí

    # 8. Crear columna Total_Delitos
    df_final["Total_Delitos"] = df_final["robos"] + df_final["secuestros"]
    
    # Asegurar que los datos para K-Means no sean cero para evitar problemas de escalabilidad.
    # En este caso, el notebook usa todo el set, así que lo replicaremos.
    
    # 9. Aplicar K-Means
    if df_final["Total_Delitos"].nunique() > 3:
        kmeans = KMeans(n_clusters=3, random_state=42, n_init=10)
        df_final["cluster"] = kmeans.fit_predict(df_final[["Total_Delitos"]])

        # Ordenar centroides de menor a mayor para asignar etiquetas
        centroids = kmeans.cluster_centers_.flatten()
        sorted_idx = np.argsort(centroids)
        labels_map = {sorted_idx[0]: "Bajo", sorted_idx[1]: "Medio", sorted_idx[2]: "Alto"}
        df_final["grado"] = df_final["cluster"].map(labels_map)
        df_final = df_final.drop(columns=["cluster"])
    else:
        # Asignación simple si hay muy poca variación
        df_final["grado"] = np.select(
            [df_final["Total_Delitos"] > 100, df_final["Total_Delitos"] > 10],
            ["Alto", "Medio"],
            default="Bajo"
        )
        
    # 10. Limpieza y formato final para la DB
    df_delitos = df_final.rename(columns={
        "Cve. Municipio": "id_municipio",
        "Entidad": "nom_estado",
        "Municipio": "nom_municipio",
        "fecha": "fecha_registro",
    }).drop(columns=["Total_Delitos", "nom_estado", "nom_municipio"]) # Se eliminan para el insert de 'delitos'

    # La columna 'id_fecha' es un SERIAL en la DB, no debe incluirse en el INSERT.
    # El notebook lo usa como un índice temporal.
    df_delitos['robos'] = df_delitos['robos'].astype(int)
    df_delitos['secuestros'] = df_delitos['secuestros'].astype(int)
    
    # Devolver el DataFrame final listo para la carga
    return df_delitos

# Ruta de carga de CSV
@app.route('/upload_csv', methods=['POST'])
def upload_csv():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    if 'file' not in request.files:
        flash('No se seleccionó ningún archivo.', 'danger')
        return redirect(url_for('dashboard'))

    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        flash('Por favor, suba un archivo CSV válido.', 'danger')
        return redirect(url_for('dashboard'))

    # Leer el archivo en memoria
    file_stream = io.StringIO(file.stream.read().decode("latin-1"))

    try:
        # Procesar los datos
        df_delitos = process_data_for_db(file_stream)

        # Cargar a la DB
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            
            # Preparar el JSON para el insert masivo. Los datos se envían como JSON
            # y se usa el comando COPY FROM STDIN de PostgreSQL para la carga rápida.
            # Convertir el DataFrame a un formato de lista de tuplas/filas
            data_to_insert = [tuple(row) for row in df_delitos.itertuples(index=False)]
            
            # Se usa una sentencia INSERT para cada fila, es más lento pero más sencillo
            # para asegurar la correcta inserción con el SERIAL id_fecha.
            insert_query = """
                INSERT INTO delitos (id_municipio, fecha_registro, robos, secuestros, grado)
                VALUES (%s, %s, %s, %s, %s);
            """
            
            # Ejecutar la inserción
            cur.executemany(insert_query, data_to_insert)
            conn.commit()
            cur.close()
            flash(f'Datos procesados y cargados exitosamente a la tabla "delitos". Total de registros: {len(df_delitos)}.', 'success')
        else:
            flash('Error de conexión con la base de datos.', 'danger')

    except Exception as e:
        flash(f'Error en el procesamiento o carga de datos: {e}', 'danger')
        
    return redirect(url_for('dashboard'))

# Ejecutar la app
if __name__ == '__main__':
    # Usar un puerto dinámico en Railway
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)