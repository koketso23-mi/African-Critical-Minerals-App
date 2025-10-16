from flask import Flask, render_template, request, redirect, url_for, session
import pandas as pd
import folium
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Change for production

# Load all CSVs with error handling
try:
    minerals_df = pd.read_csv('data/minerals.csv')
    extra_minerals_df = pd.read_csv('data/extra_minerals.csv')
    minerals_df = pd.concat([minerals_df, extra_minerals_df], ignore_index=True)  # Merge XLSX data
    minerals = minerals_df.set_index('MineralName').to_dict('index')
except Exception as e:
    print(f"Error loading minerals: {e}")
    minerals = {}  # Fallback empty dict

try:
    countries_df = pd.read_csv('data/countries.csv')
    countries = countries_df.set_index('CountryName').to_dict('index')
except Exception as e:
    print(f"Error loading countries: {e}")
    countries = {}

try:
    production_df = pd.read_csv('data/production_stats.csv')
    # Fixed merge: Index lookup DFs on IDs for proper names
    minerals_indexed = minerals_df.set_index('MineralID')
    countries_indexed = countries_df.set_index('CountryID')
    production_df = production_df.merge(minerals_indexed[['MineralName']], left_on='MineralID', right_index=True, how='left')
    production_df = production_df.merge(countries_indexed[['CountryName']], left_on='CountryID', right_index=True, how='left')
    df = production_df.rename(columns={'MineralName': 'mineral', 'CountryName': 'country'})
except Exception as e:
    print(f"Error loading production: {e}")
    df = pd.DataFrame()  # Empty DF

try:
    users_df = pd.read_csv('data/users.csv')
    users = users_df.set_index('Username').to_dict('index')
except Exception as e:
    print(f"Error loading users: {e}")
    users = {}

try:
    roles_df = pd.read_csv('data/roles.csv')
    PERMISSIONS = {}
    # helper mapping of keywords to permission flags
    keyword_map = {
        'profile': 'profiles',
        'profiles': 'profiles',
        'chart': 'charts',
        'charts': 'charts',
        'export': 'export',
        'exports': 'export',
        'production': 'production',
        'mineral': 'database',
        'database': 'database',
        'insight': 'insights',
        'insights': 'insights',
        'map': 'map',
        'all': 'all',
        'admin': 'all',
        'administrator': 'all'
    }
    for _, row in roles_df.iterrows():
        role_name = row['RoleName']
        perms_str = str(row.get('Permissions', '')).lower()
        flags = set()
        # If the permission cell contains 'full' or 'all', grant all
        if 'full' in perms_str or 'all access' in perms_str or 'full access' in perms_str:
            flags.add('all')
        else:
            # search for keywords
            for kw, flag in keyword_map.items():
                if kw in perms_str:
                    flags.add(flag)
        PERMISSIONS[role_name] = sorted(list(flags))
    # Ensure Researchers can view the map and export by default (policy override)
    if 'Researcher' in PERMISSIONS:
        if 'map' not in PERMISSIONS['Researcher']:
            PERMISSIONS['Researcher'].append('map')
        if 'export' not in PERMISSIONS['Researcher']:
            PERMISSIONS['Researcher'].append('export')
    # Make PERMISSIONS available in Jinja templates
    app.jinja_env.globals.update(PERMISSIONS=PERMISSIONS)
except Exception as e:
    print(f"Error loading roles: {e}")
    PERMISSIONS = {}

try:
    sites_df = pd.read_csv('data/sites.csv')
    # Fixed merge for sites (same indexing)
    minerals_indexed = minerals_df.set_index('MineralID')
    countries_indexed = countries_df.set_index('CountryID')
    sites_df = sites_df.merge(minerals_indexed[['MineralName']], left_on='MineralID', right_index=True, how='left')
    sites_df = sites_df.merge(countries_indexed[['CountryName']], left_on='CountryID', right_index=True, how='left')
    sites = sites_df.to_dict('records')
except Exception as e:
    print(f"Error loading sites: {e}")
    sites = []

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users and users[username]['PasswordHash'] == password:
            session['user'] = username
            role_id = users[username]['RoleID']
            role_name = roles_df[roles_df['RoleID'] == role_id]['RoleName'].iloc[0] if not roles_df.empty else 'Unknown'
            session['role'] = role_name
            # Auto-redirect to dashboard with success message
            return redirect(url_for('dashboard', success='Login successful!'))
        else:
            error = 'Invalid credentials. Try again.'
            return render_template('login.html', error=error)
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/dashboard')
def dashboard():
    success = request.args.get('success')  # Get success from redirect URL
    if 'user' not in session:
        return redirect(url_for('login'))
    role = session['role']
    allowed_features = []
    is_admin = False
    if 'all' in PERMISSIONS.get(role, []) or 'database' in PERMISSIONS.get(role, []):
        allowed_features.append('database')
    if 'all' in PERMISSIONS.get(role, []) or 'profiles' in PERMISSIONS.get(role, []):
        allowed_features.append('profiles')
    if 'all' in PERMISSIONS.get(role, []) or 'charts' in PERMISSIONS.get(role, []):
        allowed_features.append('charts')
    if 'all' in PERMISSIONS.get(role, []) or 'map' in PERMISSIONS.get(role, []):
        allowed_features.append('map')
    if 'all' in PERMISSIONS.get(role, []):
        is_admin = True
    num_countries = len(countries)
    num_minerals = len(minerals)
    num_sites = len(sites)
    return render_template('dashboard.html', role=role, features=allowed_features, success=success, num_countries=num_countries, num_minerals=num_minerals, num_sites=num_sites, is_admin=is_admin)



# Admin panel for editing, adding, and deleting data
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'user' not in session or 'all' not in PERMISSIONS.get(session['role'], []):
        return redirect(url_for('dashboard'))
    message = None
    global minerals, countries, sites
    if request.method == 'POST':
        action = request.form.get('action')
        # Mineral edit
        if action == 'edit_mineral':
            mineral_name = request.form.get('mineral_name')
            description = request.form.get('description')
            price = request.form.get('market_price')
            if mineral_name in minerals:
                minerals[mineral_name]['Description'] = description
                minerals[mineral_name]['MarketPriceUSD_per_tonne'] = price
                message = f"Updated {mineral_name}. (Note: Changes are in-memory and not persisted to CSV.)"
            else:
                message = f"Mineral {mineral_name} not found."
        # Mineral delete
        elif action == 'delete_mineral':
            mineral_name = request.form.get('mineral_name')
            if mineral_name in minerals:
                del minerals[mineral_name]
                message = f"Deleted {mineral_name}. (In-memory only.)"
            else:
                message = f"Mineral {mineral_name} not found."
        # Add country
        elif action == 'add_country':
            country_name = request.form.get('country_name')
            gdp = request.form.get('gdp')
            mining_revenue = request.form.get('mining_revenue')
            key_projects = request.form.get('key_projects')
            if country_name and country_name not in countries:
                countries[country_name] = {
                    'GDP_BillionUSD': gdp,
                    'MiningRevenue_BillionUSD': mining_revenue,
                    'KeyProjects': key_projects
                }
                message = f"Added country {country_name}. (In-memory only.)"
            else:
                message = f"Country {country_name} already exists or invalid."
        # Delete country
        elif action == 'delete_country':
            country_name = request.form.get('country_name')
            if country_name in countries:
                del countries[country_name]
                message = f"Deleted country {country_name}. (In-memory only.)"
            else:
                message = f"Country {country_name} not found."
        # Add site
        elif action == 'add_site':
            site_name = request.form.get('site_name')
            country_name = request.form.get('site_country')
            mineral_name = request.form.get('site_mineral')
            latitude = request.form.get('latitude')
            longitude = request.form.get('longitude')
            production = request.form.get('production')
            if site_name and country_name in countries and mineral_name in minerals:
                new_site = {
                    'SiteName': site_name,
                    'CountryName': country_name,
                    'MineralName': mineral_name,
                    'Latitude': float(latitude),
                    'Longitude': float(longitude),
                    'Production_tonnes': int(production)
                }
                sites.append(new_site)
                message = f"Added site {site_name}. (In-memory only.)"
            else:
                message = f"Invalid site data or missing country/mineral."
        # Delete site
        elif action == 'delete_site':
            site_name = request.form.get('site_name')
            found = False
            for i, s in enumerate(sites):
                if s.get('SiteName') == site_name:
                    del sites[i]
                    found = True
                    message = f"Deleted site {site_name}. (In-memory only.)"
                    break
            if not found:
                message = f"Site {site_name} not found."
        # Preview site coordinates on small map (admin)
        elif action == 'preview_site':
            site_name = request.form.get('site_name_edit')
            lat = request.form.get('edit_latitude')
            lon = request.form.get('edit_longitude')
            try:
                lat_f = float(lat)
                lon_f = float(lon)
                # small folium map centered on the preview coordinates
                m_preview = folium.Map(location=[lat_f, lon_f], zoom_start=9, tiles='OpenStreetMap')
                folium.Marker([lat_f, lon_f], popup=f"Preview: {site_name}").add_to(m_preview)
                map_html_admin = m_preview._repr_html_()
            except Exception:
                map_html_admin = None
                message = 'Invalid preview coordinates.'
            return render_template('admin.html', minerals=minerals, countries=countries, sites=sites, message=message, map_html_admin=map_html_admin)
        # Save edited coordinates
        elif action == 'save_site_coords':
            site_name = request.form.get('site_name_edit')
            lat = request.form.get('edit_latitude')
            lon = request.form.get('edit_longitude')
            try:
                lat_f = float(lat)
                lon_f = float(lon)
                updated = False
                for s in sites:
                    if s.get('SiteName') == site_name:
                        s['Latitude'] = lat_f
                        s['Longitude'] = lon_f
                        updated = True
                        message = f"Updated coordinates for {site_name}."
                        break
                if not updated:
                    message = f"Site {site_name} not found."
            except Exception:
                message = 'Invalid coordinates; update failed.'
    return render_template('admin.html', minerals=minerals, countries=countries, sites=sites, message=message)


# In-memory insights storage
insights = []

@app.route('/mineral_database', methods=['GET', 'POST'])
def mineral_database():
    if 'user' not in session or ('database' not in PERMISSIONS.get(session['role'], []) and 'all' not in PERMISSIONS.get(session['role'], [])):
        return redirect(url_for('dashboard'))
    message = None
    search_query = request.args.get('search', '').strip().lower()
    filtered_minerals = minerals
    if search_query:
        filtered_minerals = {k: v for k, v in minerals.items() if search_query in k.lower() or search_query in v.get('Description','').lower()}
    # Allow researchers to add insights
    if request.method == 'POST' and 'insight' in request.form:
        user = session.get('user', 'unknown')
        role = session.get('role', '')
        # Only allow researchers (or those with 'insights' permission) to add insights
        if role == 'Researcher' or 'insights' in PERMISSIONS.get(role, []):
            insight = request.form.get('insight')
            if insight:
                insights.append({'user': user, 'insight': insight, 'type': 'mineral'})
                message = 'Insight added.'
        else:
            message = 'You do not have permission to add insights.'
    return render_template('mineral_database.html', minerals=filtered_minerals, insights=[i for i in insights if i['type']=='mineral'], message=message, search_query=search_query)

# Download PDF of mineral data (researcher only)
@app.route('/download/minerals.pdf')
def download_minerals_pdf():
    # Only Researcher role may download PDFs
    if 'user' not in session or session.get('role') != 'Researcher':
        return redirect(url_for('dashboard'))
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.setFont("Helvetica", 12)
    y = 750
    p.drawString(30, y, "Mineral Data Export")
    y -= 30
    for mineral, info in minerals.items():
        p.drawString(30, y, f"{mineral}: {info.get('Description','')} | ${info.get('MarketPriceUSD_per_tonne','')}")
        y -= 20
        if y < 50:
            p.showPage()
            y = 750
    p.save()
    buffer.seek(0)
    return app.response_class(buffer, mimetype='application/pdf', headers={"Content-Disposition": "attachment;filename=minerals.pdf"})

# Download PDF of country data (researcher only)
@app.route('/download/countries.pdf')
def download_countries_pdf():
    # Only Researcher role may download PDFs
    if 'user' not in session or session.get('role') != 'Researcher':
        return redirect(url_for('dashboard'))
    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.setFont("Helvetica", 12)
    y = 750
    p.drawString(30, y, "Country Data Export")
    y -= 30
    for country, info in countries.items():
        p.drawString(30, y, f"{country}: GDP ${info.get('GDP_BillionUSD','')}B | Mining Revenue ${info.get('MiningRevenue_BillionUSD','')}B | Projects: {info.get('KeyProjects','')}")
        y -= 20
        if y < 50:
            p.showPage()
            y = 750
    p.save()
    buffer.seek(0)
    return app.response_class(buffer, mimetype='application/pdf', headers={"Content-Disposition": "attachment;filename=countries.pdf"})


@app.route('/country_profiles', methods=['GET', 'POST'])
def country_profiles():
    if 'user' not in session or ('profiles' not in PERMISSIONS.get(session['role'], []) and 'all' not in PERMISSIONS.get(session['role'], [])):
        return redirect(url_for('dashboard'))
    message = None
    search_query = request.args.get('search', '').strip().lower()
    filtered_countries = countries
    if search_query:
        filtered_countries = {k: v for k, v in countries.items() if search_query in k.lower() or search_query in v.get('KeyProjects','').lower()}
    # Allow researchers to add insights
    if request.method == 'POST' and 'insight' in request.form:
        user = session.get('user', 'unknown')
        role = session.get('role', '')
        # Only allow researchers (or those with 'insights' permission) to add insights
        if role == 'Researcher' or 'insights' in PERMISSIONS.get(role, []):
            insight = request.form.get('insight')
            if insight:
                insights.append({'user': user, 'insight': insight, 'type': 'country'})
                message = 'Insight added.'
        else:
            message = 'You do not have permission to add insights.'
    return render_template('country_profiles.html', countries=filtered_countries, insights=[i for i in insights if i['type']=='country'], message=message, search_query=search_query)

@app.route('/interactive_charts')
def interactive_charts():
    if 'user' not in session or ('charts' not in PERMISSIONS.get(session['role'], []) and 'all' not in PERMISSIONS.get(session['role'], [])):
        return redirect(url_for('dashboard'))
    # Basic filters for interactivity (Appendix A)
    mineral_filter = request.args.get('mineral', 'all')
    country_filter = request.args.get('country', 'all')
    filtered_df = df.copy()
    if mineral_filter != 'all':
        filtered_df = filtered_df[filtered_df['mineral'] == mineral_filter]
    if country_filter != 'all':
        filtered_df = filtered_df[filtered_df['country'] == country_filter]
    # Fixed charts: Ensure data has names/values, fallback to full df if empty
    if filtered_df.empty:
        filtered_df = df.copy()
    # Check if columns exist, fallback to IDs if merge failed
    color_col = 'mineral' if 'mineral' in filtered_df.columns else 'MineralID'
    country_col = 'country' if 'country' in filtered_df.columns else 'CountryID'
    # Choose a modern color palette
    palette = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b']

    # Production as bar (categorical)
    fig_prod = px.bar(filtered_df, x='Year', y='Production_tonnes', color=color_col, barmode='group',
                      title=f'Production Trends {mineral_filter if mineral_filter != "all" else ""} in {country_filter if country_filter != "all" else ""}',
                      hover_data=[country_col, 'ExportValue_BillionUSD'], labels={'Production_tonnes': 'Tonnes'},
                      color_discrete_sequence=palette)
    fig_prod.update_layout(template='plotly_white')
    chart_div = fig_prod.to_html(full_html=False)

    # Export as line (trends)
    fig_export = px.line(filtered_df, x='Year', y='ExportValue_BillionUSD', color=color_col,
                         title=f'Export Value Trends {mineral_filter if mineral_filter != "all" else ""} in {country_filter if country_filter != "all" else ""}',
                         hover_data=[country_col, 'Production_tonnes'], labels={'ExportValue_BillionUSD': 'Billion USD'},
                         color_discrete_sequence=palette)
    fig_export.update_traces(mode='lines+markers')
    fig_export.update_layout(template='plotly_white')
    price_div = fig_export.to_html(full_html=False)

    # Additional chart 1: Production share pie by mineral (or country if mineral selected)
    try:
        if mineral_filter == 'all':
            pie_df = filtered_df.groupby('mineral', as_index=False).sum()
            names = pie_df['mineral']
            values = pie_df['Production_tonnes']
            pie_title = 'Production Share by Mineral'
        else:
            pie_df = filtered_df.groupby('country', as_index=False).sum()
            names = pie_df['country']
            values = pie_df['Production_tonnes']
            pie_title = 'Production Share by Country'
    except Exception:
        names = filtered_df['mineral'] if 'mineral' in filtered_df.columns else filtered_df.get('MineralID', [])
        values = filtered_df['Production_tonnes'] if 'Production_tonnes' in filtered_df.columns else []
        pie_title = 'Production Share'
    fig_pie = px.pie(names=names, values=values, title=pie_title, color_discrete_sequence=palette)
    fig_pie.update_layout(template='plotly_white')
    pie_div = fig_pie.to_html(full_html=False)

    # Additional chart 2: Combined production (bar) and export (line) over years
    yearly = filtered_df.groupby('Year', as_index=False).sum()
    fig_combo = make_subplots(specs=[[{"secondary_y": True}]])
    fig_combo.add_trace(go.Bar(x=yearly['Year'], y=yearly['Production_tonnes'], name='Production (tonnes)', marker_color=palette[0]))
    fig_combo.add_trace(go.Scatter(x=yearly['Year'], y=yearly['ExportValue_BillionUSD'], name='Export Value (B USD)', mode='lines+markers', marker_color=palette[1]), secondary_y=True)
    fig_combo.update_layout(title_text='Production vs Export Value (Yearly)', template='plotly_white')
    fig_combo.update_xaxes(title_text='Year')
    fig_combo.update_yaxes(title_text='Production (tonnes)', secondary_y=False)
    fig_combo.update_yaxes(title_text='Export Value (B USD)', secondary_y=True)
    combo_div = fig_combo.to_html(full_html=False)

    return render_template('interactive_charts.html', chart_div=chart_div, price_div=price_div, pie_div=pie_div, combo_div=combo_div, minerals=list(minerals.keys()), countries=list(countries.keys()))

@app.route('/geographical_map')
def geographical_map():
    if 'user' not in session or ('map' not in PERMISSIONS.get(session['role'], []) and 'all' not in PERMISSIONS.get(session['role'], [])):
        return redirect(url_for('dashboard'))
    # Basic filter for map (Appendix A: alternatives/deposits)
    mineral_filter = request.args.get('mineral', 'all')
    filtered_sites = sites
    if mineral_filter != 'all':
        filtered_sites = [s for s in sites if s.get('MineralName') == mineral_filter]
    m = folium.Map(location=[0, 20], zoom_start=3, tiles=None, attr='Google Maps (English)')
    # Google Satellite default (English labels, real imagery)
    folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=s,h&x={x}&y={y}&z={z}&hl=en', 
                     attr='Google Satellite (English)', name='Google Satellite (English)', overlay=False, control=True).add_to(m)
    # Google Roadmap for streets (English)
    folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}&hl=en', 
                     attr='Google Roadmap (English)', name='Google Roadmap (English)', overlay=False, control=True).add_to(m)
    folium.LayerControl().add_to(m)
    coords = []
    for site in filtered_sites:
        # Ensure numeric coordinates and robust handling of swapped values
        try:
            lat = float(site.get('Latitude', 0))
            lon = float(site.get('Longitude', 0))
        except Exception:
            # Skip site if coordinates are not parseable
            continue

        # If values look swapped (lat outside [-90,90] but lon inside), swap them
        if (abs(lat) > 90 and abs(lon) <= 90) or (abs(lon) > 180) or (abs(lat) > 180 and abs(lon) <= 180):
            lat, lon = lon, lat

        # After potential swap, verify ranges
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            # Skip invalid coordinates
            continue

        coords.append((lat, lon))
        popup = f"{site.get('SiteName','Unknown Site')} - {site.get('MineralName', 'Unknown')} in {site.get('CountryName', 'Unknown')} ({site.get('Production_tonnes', 'n/a')} tonnes)"
        folium.Marker([lat, lon], popup=popup).add_to(m)

    # Fit map to markers if we have any, otherwise keep default
    if coords:
        m.fit_bounds([ [min(r[0] for r in coords), min(r[1] for r in coords)], [max(r[0] for r in coords), max(r[1] for r in coords)] ])
    map_html = m._repr_html_()
    return render_template('geographical_map.html', map_html=map_html, minerals=list(minerals.keys()))

if __name__ == '__main__':
    app.run(debug=True)