# %%
import os
import psycopg2
import pandas as pd
import geopandas as gpd
from shapely.geometry import shape

from config import DB_VISION_ZERO, DB_MOPED

# %%
def get_data(query, cursor):
    """
    Get data from database
    """
    cursor.execute(query)
    data = cursor.fetchall()
    field_names = [i[0] for i in cursor.description]
    df = pd.DataFrame(data, columns=field_names)

    return df

conn_vz = psycopg2.connect(
    dbname = DB_VISION_ZERO['dbname'],
    user = DB_VISION_ZERO["user"],
    host = DB_VISION_ZERO["host"],
    password = DB_VISION_ZERO["password"],
    port=5432
)

conn_moped = psycopg2.connect(
    dbname = DB_MOPED["dbname"],
    user = DB_MOPED["user"],
    host = DB_MOPED["host"],
    password = DB_MOPED["password"],
    port = 5432
)

cursor_vz = conn_vz.cursor()
cursor_moped = conn_moped.cursor()

# %% [markdown]
# # Moped processing

# %%
# Creating moped dataframe
QUERY_MOPED = """SELECT project_id, project_component_id, geometry, 
line_geometry, substantial_completion_date,
component_name, component_name_full, component_subtype, 
component_work_types, type_name FROM component_arcgis_online_view"""

# Creating moped dataframe
df_moped = get_data(QUERY_MOPED, cursor_moped)

# %%
# Data frame info
df_moped.info()

# %%
# Dropping observations where substantial completion date or line geometry is absent
df_moped_filter = df_moped.dropna(subset=['substantial_completion_date', 'line_geometry'])
df_moped_filter.head()

# %%
df_moped_filter.info()

# %%
# Convert timestamp columns to string
timestamp_columns = ["substantial_completion_date"]

for col in timestamp_columns:
    df_moped_filter.loc[:, col] = df_moped_filter[col].astype(str)

# Apply the geometry transformation
df_moped_filter.loc[:, "geometry"] = df_moped_filter["geometry"].apply(lambda x: shape(x) if x is not None else None)
df_moped_filter.loc[:, "line_geometry"] = df_moped_filter["line_geometry"].apply(lambda x: shape(x) if x is not None else None)

# Create GeoDataFrame
gdf_moped = gpd.GeoDataFrame(df_moped_filter, geometry="geometry")

# %%
# Adding a unique ID column
gdf_moped.insert(0, 'moped_component_id', range(1, 1 + len(gdf_moped)))

# %%
gdf_moped.head()

# %% [markdown]
# # VisionZero processing

# %%
# Creaing vision zero dataframe
QUERY_CRASH_DATA = """SELECT crash_id, crash_fatal_fl, crash_date,
road_constr_zone_fl, latitude, longitude, tot_injry_cnt, 
death_cnt, est_comp_cost FROM atd_txdot_crashes"""

df_vz = get_data(QUERY_CRASH_DATA, cursor_vz)

# %%
df_vz.info()

# %%
# Keepiing only those observations where x-y coordinates are present
df_vz_filter = df_vz[df_vz['latitude'].notnull() & df_vz['longitude'].notnull()]

# %%
df_vz_filter.info()

# %%
# Convert timestamp columns to string
timestamp_columns = ["crash_date"]

for col in timestamp_columns:
    df_vz_filter.loc[:, col] = df_vz_filter[col].astype(str)

# %%
# Creating geodataframe
gdf_vz = gpd.GeoDataFrame(df_vz_filter,
                          geometry=gpd.points_from_xy(df_vz_filter.longitude,
                                                      df_vz_filter.latitude),
                                                      crs='EPSG:4326')

gdf_vz.head()

# %% [markdown]
# # Spatial join

# %%
# Creating buffer for joining
gdf_moped = gdf_moped.set_geometry('line_geometry')
gdf_moped.set_crs(epsg=4326, inplace=True)
gdf_moped_proj = gdf_moped.to_crs(epsg=32614)
buffer_distance = 20

gdf_moped_proj = gdf_moped.to_crs(epsg=32614)

# %%
gdf_moped_proj['buffered_geometry'] = gdf_moped_proj.geometry.buffer(buffer_distance)
buffered_moped_gdf = gdf_moped_proj.set_geometry('buffered_geometry').to_crs('EPSG:4326')

# %% [markdown]
# Buffered geometry results in line strings and multi line strings being turned into polygons

# %%
buffered_moped_gdf.head()

# %%
# Spatial join
crashes_near_projects = gpd.sjoin(gdf_vz, buffered_moped_gdf, how='inner')

# Creating a unique ID column
crashes_near_projects['crash_project_component_id'] = crashes_near_projects['crash_id'].astype(str) + "-" + crashes_near_projects['project_id'].astype(str) + "-" + crashes_near_projects['project_component_id'].astype(str)

# %%
print('Number of unique crashes in merged dataset:', crashes_near_projects['crash_id'].nunique())
print('Number of unique moped component IDs in merged dataset:', crashes_near_projects['moped_component_id'].nunique())

# %%
crashes_near_projects.info()

# %% [markdown]
# # Analysis

# %%
# Formatting crash date
crashes_near_projects['crash_date'] = pd.to_datetime(crashes_near_projects['crash_date'], errors='coerce').dt.tz_localize('UTC', nonexistent='NaT', ambiguous='NaT').dt.tz_convert('UTC')

# %%
crashes_near_projects.info()

# %%
# Re-arranging columns
# unique identifier for each observation
crashes_near_projects.insert(0, 'crash_project_component_id', crashes_near_projects.pop('crash_project_component_id'))

# moped_component_id
crashes_near_projects.insert(2, 'moped_component_id', crashes_near_projects.pop('moped_component_id'))

# crash_date
crashes_near_projects.insert(4, 'crash_date', crashes_near_projects.pop('crash_date'))

# project compoenent ID
crashes_near_projects.insert(3, 'project_component_id', crashes_near_projects.pop('project_component_id'))

# Substantial completion date
crashes_near_projects.insert(5, 'substantial_completion_date', crashes_near_projects.pop('substantial_completion_date'))

# %%
# Creating a binary version of the fatality column
crashes_near_projects['crash_fatal_binary'] = crashes_near_projects['crash_fatal_fl'].apply(lambda x: 1 if x == "Y" else 0)
crashes_near_projects.pop('crash_fatal_fl')

# Rearranging the crash fatal binary column 
crashes_near_projects.insert(4, 'crash_fatal_binary', crashes_near_projects.pop('crash_fatal_binary'))

# %%
crashes_near_projects.head()

# %%
crashes_near_projects['crash_fatal_binary'].value_counts()

# %%
crashes_near_projects.info()

# %%
# Creating indicator variables for crash occuring pre and post completion of mobility project
crashes_near_projects.insert(7, 'crash_pre_completion', crashes_near_projects['crash_date'] < crashes_near_projects['substantial_completion_date'])
crashes_near_projects.insert(8, 'crash_post_completion', crashes_near_projects['crash_date'] > crashes_near_projects['substantial_completion_date'])

# %%
# Creating time difference variables
crashes_near_projects.insert(9, 'crash_project_date_diff', crashes_near_projects['substantial_completion_date'] - crashes_near_projects['crash_date'])

# %%
# Converting estimated comp cost to float format
crashes_near_projects['est_comp_cost'] = crashes_near_projects['est_comp_cost'].map(lambda x: float(x))

crashes_near_projects.info()

# %%
# Function to calculate duration in years
def calculate_duration(df, date_col1, date_col2):
    duration = (df[date_col2] - df[date_col1]).dt.total_seconds() / (365.25 * 24 * 3600)
    return duration

crashes_near_projects['pre_completion_duration'] = crashes_near_projects['crash_pre_completion'] * calculate_duration(crashes_near_projects, 'crash_date', 'substantial_completion_date')
crashes_near_projects['post_completion_duration'] = crashes_near_projects['crash_post_completion'] * calculate_duration(crashes_near_projects, 'substantial_completion_date', 'crash_date')

pre_completion_stats = crashes_near_projects[crashes_near_projects['crash_pre_completion'] == True].groupby('moped_component_id').agg({
    'crash_id': 'count',
    'pre_completion_duration': 'sum',
    'crash_fatal_binary': 'sum',
    'tot_injry_cnt': 'sum',
    'death_cnt': 'sum',
    'est_comp_cost': 'sum'
}).rename(columns={'crash_id': 'pre_crash_count',
                   'crash_fatal_binary': 'pre_fatal_crash_count',
                   'tot_injry_cnt': 'pre_total_injury_count',
                   'death_cnt': 'pre_total_death_count',
                   'est_comp_cost': 'pre_est_comp_cost'}).reset_index()

post_completion_stats = crashes_near_projects[crashes_near_projects['crash_post_completion'] == True].groupby('moped_component_id').agg({
    'crash_id': 'count',
    'post_completion_duration': 'sum',
    'crash_fatal_binary': 'sum',
    'tot_injry_cnt': 'sum',
    'death_cnt': 'sum',
    'est_comp_cost': 'sum'
}).rename(columns={'crash_id': 'post_crash_count',
                   'crash_fatal_binary': 'post_fatal_crash_count',
                   'tot_injry_cnt': 'post_total_injury_count',
                   'death_cnt': 'post_total_death_count',
                   'est_comp_cost': 'post_est_comp_cost'}).reset_index()


# Merging
annualized_statistics = pre_completion_stats.merge(post_completion_stats, on='moped_component_id', how='outer').fillna(0)

# Calculating annualized statistics
# Crash rate
annualized_statistics['pre_annualized_crash_rate'] = annualized_statistics['pre_crash_count'] / annualized_statistics['pre_completion_duration']
annualized_statistics['post_annualized_crash_rate'] = annualized_statistics['post_crash_count'] / annualized_statistics['post_completion_duration']

# Fatality
annualized_statistics['pre_annualized_fatal_crash_rate'] = annualized_statistics['pre_fatal_crash_count'] / annualized_statistics['pre_completion_duration']
annualized_statistics['post_annualized_fatal_crash_rate'] = annualized_statistics['post_fatal_crash_count'] / annualized_statistics['post_completion_duration']

# Injury count
annualized_statistics['pre_annualized_injury_rate'] = annualized_statistics['pre_total_injury_count'] / annualized_statistics['pre_completion_duration']
annualized_statistics['post_annualized_injury_rate'] = annualized_statistics['post_total_injury_count'] / annualized_statistics['post_completion_duration']

# Death count
annualized_statistics['pre_annualized_death_rate'] = annualized_statistics['pre_total_death_count'] / annualized_statistics['pre_completion_duration']
annualized_statistics['post_annualized_death_rate'] = annualized_statistics['post_total_death_count'] / annualized_statistics['post_completion_duration']

# Estimated cost
annualized_statistics['pre_annualized_cost'] = annualized_statistics['pre_est_comp_cost'] / annualized_statistics['pre_completion_duration']
annualized_statistics['post_annualized_cost'] = annualized_statistics['post_est_comp_cost'] / annualized_statistics['post_completion_duration']

# %%
# Getting completion date for each moped component id
completion_dates = crashes_near_projects.groupby('moped_component_id')['substantial_completion_date'].first().reset_index()

# Merging into the annualized crash rate DataFrame
annualized_statistics = annualized_statistics.merge(completion_dates, on='moped_component_id', how='left')

# %%
annualized_statistics = annualized_statistics[['moped_component_id',
                                               'substantial_completion_date', 
                                               'pre_annualized_crash_rate', 
                                               'post_annualized_crash_rate',
                                               'pre_annualized_fatal_crash_rate',
                                               'post_annualized_fatal_crash_rate',
                                               'pre_annualized_injury_rate',
                                               'post_annualized_injury_rate',
                                               'pre_annualized_death_rate',
                                               'post_annualized_death_rate',
                                               'pre_annualized_cost',
                                               'post_annualized_cost'
                                               ]]

# %%
annualized_statistics.info()

# %%
# Creating difference columns between pre and post
annualized_statistics.insert(4, 'delta_crash_rate', annualized_statistics['post_annualized_crash_rate']  - annualized_statistics['pre_annualized_crash_rate'])
annualized_statistics.insert(7, 'delta_fatal_crash_rate', annualized_statistics['post_annualized_fatal_crash_rate']  - annualized_statistics['pre_annualized_fatal_crash_rate'])
annualized_statistics.insert(10, 'delta_injury_rate', annualized_statistics['post_annualized_injury_rate']  - annualized_statistics['pre_annualized_injury_rate'])
annualized_statistics.insert(13, 'delta_death_rate', annualized_statistics['post_annualized_death_rate']  - annualized_statistics['pre_annualized_death_rate'])
annualized_statistics.insert(16, 'delta_comp_cost', annualized_statistics['post_annualized_cost']  - annualized_statistics['pre_annualized_cost'])


# %%
# Merging additional information such as component name, type, etc.
additional_info = crashes_near_projects[['moped_component_id', 
                                         'component_name', 
                                         'component_name_full', 
                                         'component_subtype',
                                         'component_work_types', 
                                         'type_name',
                                         'line_geometry']].drop_duplicates()

annualized_statistics = annualized_statistics.merge(additional_info, on='moped_component_id', how='left')

# %%
annualized_statistics.info()

# %%
# Reordering
all_columns = annualized_statistics.columns.tolist()

first_column = all_columns[0]
last_six = all_columns[-6:]
new_order = [first_column] + last_six + all_columns[1:-6]
annualized_statistics = annualized_statistics[new_order]

# %%
annualized_statistics.to_csv('../Output/annualized_statistics.csv', na_rep="NA", index=False)

# %%


