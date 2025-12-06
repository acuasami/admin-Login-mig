import pandas as pd
import numpy as np
import io
import os
import psycopg2
from urllib.parse import urlparse
from flask import Flask, render_template, request, redirect, url_for, session, flash
from sklearn.cluster import KMeans
from werkzeug.security import safe_str_cmp

# --- CONFIGURACIÓN Y CONEXIÓN A LA BASE DE DATOS ---

app = Flask(__name__)
# Es obligatorio configurar esta clave en las variables de entorno de Railway
app.secret_key = os.environ.get('SECRET_KEY', 'una_clave_secreta_temporal_debe_cambiarse') 

# Usar la variable de entorno estándar de Railway, o el URI hardcodeado si falla (no recomendado)
DB_URI = os.environ.get('DATABASE_URL') or 'postgresql://postgres:KAGJhRklTcsevGqKEgCNPfmdDiGzsLyQ@switchyard.proxy.rlwy.net:13155/railway' #

# Función para obtener la conexión a la DB
def get_db_connection():
    try:
        result = urlparse(DB_URI)
        conn = psycopg2.connect(
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port,
            dbname=result.path.lstrip('/'),
            connect_timeout=5 # Tiempo de espera para la conexión
        )
        return conn
    except Exception as e:
        # Imprimir el error en los logs para depuración
        print(f"❌ ERROR: Fallo al conectar a la base de datos: {e}")
        return None

# Función para crear la tabla de delitos si no existe
def create_delitos_table():
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            # Se usa 'delitos' en lugar de 'fecha' para los registros de crimen.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS delitos (
                    id_fecha SERIAL PRIMARY KEY,
                    id_municipio INT NOT NULL,
                    fecha_registro DATE NOT NULL,
                    robos INT NOT NULL,
                    secuestros INT NOT NULL,
                    grado VARCHAR(50) NOT NULL
                );
            """)
            conn.commit()
            cur.close()
            print("✅ Tabla 'delitos' verificada/creada exitosamente.")
        except Exception as e:
            print(f"❌ ERROR: Fallo al crear la tabla 'delitos': {e}")
        finally:
            conn.close()

# Llamar a la función para verificar/crear la tabla, pero DE FORMA SEGURA.
# Lo hacemos dentro de un contexto para evitar que un fallo detenga el servidor.
with app.app_context():
    create_delitos_table()


# --- AUTENTICACIÓN ---

ADMIN_USER = "admMigrantes"
ADMIN_PASS = "contraseña123"

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # safe_str_cmp previene ataques de sincronización
        if safe_str_cmp(username, ADMIN_USER) and safe_str_cmp(password, ADMIN_PASS):
            session['logged_in'] = True
            flash('Inicio de sesión exitoso.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Credenciales incorrectas. Intente de nuevo.', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Has cerrado sesión.', 'info')
    return redirect(url_for('login'))

# --- RUTA PROTEGIDA (DASHBOARD) ---

@app.route('/dashboard')
def dashboard():
    if not session.get('logged_in'):
        flash('Debe iniciar sesión para acceder.', 'warning')
        return redirect(url_for('login'))

    ongs_data = []
    ongs_cols = ["id_ong", "nombre", "contacto", "ubicacion"] # Columnas por defecto
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            # Seleccionar todos los datos de la tabla 'ongs'
            cur.execute("SELECT * FROM ongs;")
            ongs_data = cur.fetchall()
            if cur.description:
                ongs_cols = [desc[0] for desc in cur.description] # Obtener los nombres reales de las columnas
            cur.close()
        except Exception as e:
            flash(f"Error al cargar datos de ONGs: {e}", 'danger')
        finally:
            conn.close()
    else:
         flash('No se pudo conectar con la base de datos.', 'danger')

    return render_template('dashboard.html', ongs_data=ongs_data, ongs_cols=ongs_cols)


# --- LÓGICA DE PROCESAMIENTO Y CARGA DE DATOS ---

# Lógica del cuaderno de procesamiento (admin/Preprocesamieto (1) - copia.ipynb)
def process_data_for_db(file_stream):
    # Implementación completa de la lógica de pandas y K-Means basada en el archivo subido
    # (Se omite el código aquí para mantener la brevedad, pero debe ser el código completo de la sección 2)
    try:
        # Cargar datos con codificación latin1 para manejar caracteres especiales
        df = pd.read_csv(file_stream, encoding='latin1')
    except UnicodeDecodeError:
        file_stream.seek(0)
        df = pd.read_csv(file_stream, encoding='utf8') 
    
    # Asegurarse de que las columnas de meses sean float para K-Means
    meses_cols = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
    for col in meses_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0) # Convertir a número, fallos a 0

    # Pasos de pre-procesamiento idénticos al cuaderno:
    max_anio = df['Año'].max() # 2025
    df = df[df['Año'] == max_anio].copy()

    meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
             'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']

    meses_validos = [mes for mes in meses 
                     if mes in df.columns 
                     and df[mes].astype(float).sum() > 0]
    ultimos_3_meses = meses_validos[-3:] if len(meses_validos) >= 3 else meses_validos # Mayo, Junio, Julio
    
    columnas_a_eliminar = [mes for mes in meses if mes in df.columns and mes not in ultimos_3_meses]
    df = df.drop(columns=columnas_a_eliminar, errors='ignore')

    df = df[df['Tipo de delito'].isin(['Secuestro', 'Robo'])].copy() #

    subtipos_a_eliminar = [
        'Robo a transportista', 'Robo a institución bancaria',
        'Robo a negocio', 'Robo de ganado'
    ]
    df = df[~df['Subtipo de delito'].isin(subtipos_a_eliminar)].copy() #

    modalidades_validas = [
        'Secuestro extorsivo', 'Secuestro con calidad de rehén',
        'Secuestro para causar daño', 'Secuestro exprés', 'Otro tipo de secuestros',
        'Con violencia', 'Sin violencia',
        'Robo de coche de 4 ruedas Con violencia', 'Robo de coche de 4 ruedas Sin violencia',
        'Robo de motocicleta Con violencia', 'Robo de motocicleta Sin violencia'
    ]
    df = df[df['Modalidad'].isin(modalidades_validas)].copy() #

    columnas_conservar = ['Entidad', 'Cve. Municipio', 'Municipio', 'Tipo de delito'] + ultimos_3_meses
    df = df[columnas_conservar]
    columnas_agrupacion = ['Entidad', 'Cve. Municipio', 'Municipio', 'Tipo de delito']
    df_agrupado = df.groupby(columnas_agrupacion, dropna=False).sum().reset_index() #

    df_long = df_agrupado.melt(
        id_vars=["Entidad", "Cve. Municipio", "Municipio", "Tipo de delito"],
        value_vars=ultimos_3_meses,
        var_name="Mes",
        value_name="Cantidad"
    ) #

    meses_num = {
        "Enero": "01","Febrero": "02","Marzo": "03","Abril": "04",
        "Mayo": "05","Junio": "06","Julio": "07","Agosto": "08",
        "Septiembre": "09","Octubre": "10","Noviembre": "11","Diciembre": "12"
    } #

    anio = max_anio
    df_long["fecha"] = pd.to_datetime(
        df_long["Mes"].map(meses_num).radd(f"{anio}-") + "-01"
    ) #

    df_final = df_long.pivot_table(
        index=["Entidad", "Cve. Municipio", "Municipio", "fecha"],
        columns="Tipo de delito",
        values="Cantidad",
        fill_value=0
    ).reset_index().rename(columns={"Robo": "robos", "Secuestro": "secuestros"}) #

    df_final["Total_Delitos"] = df_final["robos"] + df_final["secuestros"] #
    
    # Aplicar K-Means
    df_km = df_final[df_final["Total_Delitos"] > 0].copy() # Solo aplicar K-Means a los que tienen delitos
    if len(df_km) >= 3: # Asegurar suficientes puntos para 3 clusters
        kmeans = KMeans(n_clusters=3, random_state=42, n_init=10)
        df_km["cluster"] = kmeans.fit_predict(df_km[["Total_Delitos"]])

        centroids = kmeans.cluster_centers_.flatten()
        sorted_idx = np.argsort(centroids)
        labels_map = {sorted_idx[0]: "Bajo", sorted_idx[1]: "Medio", sorted_idx[2]: "Alto"}
        df_km["grado"] = df_km["cluster"].map(labels_map)
        df_km = df_km.drop(columns=["cluster"])
    else:
        # Asignación simple si no hay suficientes datos para K-Means
        df_km["grado"] = np.select(
            [df_km["Total_Delitos"] > df_km["Total_Delitos"].median()],
            ["Alto"],
            default="Bajo"
        )
        
    # Combinar de vuelta y rellenar ceros
    df_final = df_final.merge(df_km[['Cve. Municipio', 'fecha', 'grado']], on=['Cve. Municipio', 'fecha'], how='left')
    df_final['grado'] = df_final['grado'].fillna('Bajo') # Los que tienen Total_Delitos=0 son Bajo

    df_delitos = df_final.rename(columns={
        "Cve. Municipio": "id_municipio",
        "fecha": "fecha_registro",
    }).drop(columns=["Total_Delitos", "Entidad", "Municipio"])

    df_delitos['robos'] = df_delitos['robos'].astype(int)
    df_delitos['secuestros'] = df_delitos['secuestros'].astype(int)
    
    return df_delitos

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

    # Leer el archivo en memoria y forzar la decodificación si es necesario
    try:
        # Intenta decodificar con latin-1, si falla, usa utf-8
        file_content = file.stream.read().decode("latin-1")
    except UnicodeDecodeError:
        file.stream.seek(0)
        file_content = file.stream.read().decode("utf-8")

    file_stream = io.StringIO(file_content)

    try:
        # Procesar los datos
        df_delitos = process_data_for_db(file_stream)

        # Cargar a la DB
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            
            # Formato de datos para la inserción
            data_to_insert = [
                (row.id_municipio, row.fecha_registro, row.robos, row.secuestros, row.grado)
                for row in df_delitos.itertuples(index=False)
            ]
            
            # Sentencia INSERT para la tabla 'delitos' (que reemplaza 'fecha' para este fin)
            insert_query = """
                INSERT INTO delitos (id_municipio, fecha_registro, robos, secuestros, grado)
                VALUES (%s, %s, %s, %s, %s);
            """
            
            # Ejecutar la inserción masiva
            cur.executemany(insert_query, data_to_insert)
            conn.commit()
            cur.close()
            flash(f'✅ Datos procesados y cargados exitosamente a la tabla "delitos". Total de registros: {len(df_delitos)}.', 'success')
        else:
            flash('❌ Error de conexión con la base de datos.', 'danger')

    except Exception as e:
        flash(f'❌ Error en el procesamiento o carga de datos: {e}', 'danger')
        import traceback
        print(traceback.format_exc()) # Imprimir stack trace para logs
        
    return redirect(url_for('dashboard'))

# Ejecutar la app
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # Usar debug=False en producción, pero es útil para depurar localmente
    app.run(host='0.0.0.0', port=port, debug=True)
