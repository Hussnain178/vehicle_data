import psycopg2
from psycopg2 import pool, sql
import threading
from typing import Dict, Any
from configuration.config import Config


class VehicleDatabase:
    """
    Thread-safe database class for vehicle data operations.
    Handles schema/table creation and provides insertion with duplicate checking.
    """

    _lock = threading.Lock()
    _connection_pools = {}

    host = Config.DATABASE_HOST
    port = Config.DATABASE_PORT
    user = Config.DATABASE_USER
    password = Config.DATABASE_PASSWORD
    database_name = Config.DATABASE_NAME

    # Column definitions
    STRING_COLUMNS = ['vehicle_id', 'data_source', 'listing_url', 'title', 'price', 'make', 'model', 'images',
                      'model_version', 'model_range', 'price_info', 'subtitle', 'price_label', 'trim_line',
                      'vehicle_type', 'category', 'body_type', 'make_id', 'model_id', 'model_generation_id',
                      'model_variant_id', 'motor_type_id', 'trim_line_id', 'air_conditioning_type', 'sku', 'hsn_tsn',
                      'identifier', 'power_kw', 'power_hp', 'power_display', 'power_kw_display', 'power_hp_display',
                      'displacement_ccm', 'displacement_display', 'cylinders', 'gears', 'weight', 'net_weight',
                      'fuel_type', 'fuel_category', 'primary_fuel', 'transmission', 'transmission_type',
                      'drive_train', 'has_particle_filter', 'fuel_consumption_combined', 'fuel_consumption_urban',
                      'fuel_consumption_extra_urban', 'co2_emission', 'co2_emission_combined',
                      'co2_emissions_combined_fallback', 'co2_emissions_combined_weighted', 'co2_emissions_discharged',
                      'co2_class', 'co2_class_discharged', 'emission_sticker', 'emission_standard',
                      'consumption_combined', 'consumption_combined_weighted', 'consumption_combined_discharged',
                      'consumption_electric_combined', 'consumption_electric_combined_weighted', 'consumption_city',
                      'consumption_city_discharged', 'consumption_suburban', 'consumption_suburban_discharged',
                      'consumption_rural', 'consumption_rural_discharged', 'consumption_highway',
                      'consumption_highway_discharged', 'consumption_electric_city', 'consumption_electric_suburban',
                      'consumption_electric_rural', 'consumption_electric_highway', 'environment_pkw_envkv',
                      'environment_eu_directive', 'environment_bimschv35', 'wltp', 'energy_consumption',
                      'consumption_costs', 'consumption_costs_year', 'fuel_price', 'co2_costs', 'co2_costs_average',
                      'co2_costs_high', 'co2_costs_low', 'vehicle_tax', 'engine_type', 'other_energy_source',
                      'mileage_km', 'mileage_display', 'mileage_detail', 'mileage_in_km', 'first_registration',
                      'first_registration_raw', 'first_registration_date', 'production_year', 'construction_year',
                      'last_inspection', 'next_inspection', 'last_service_date', 'last_service_mileage',
                      'last_belt_service', 'full_service_history', 'offer_type', 'condition', 'damage_condition',
                      'had_accident', 'previous_owners', 'is_rental', 'non_smoking', 'has_registration',
                      'new_driver_suitable', 'country_version', 'original_market', 'type', 'seats', 'doors',
                      'color', 'color_original', 'manufacturer_color', 'paint_type', 'upholstery', 'upholstery_color',
                      'interior', 'interior_color', 'interior_type', 'vehicle_model_id', 'vehicle_transmission',
                      'vehicle_fuel_type', 'vehicle_fuel_consumption', 'model_or_line_id', 'wheel_base',
                      'total_height', 'total_width', 'total_length', 'gross_vehicle_weight',
                      'gross_vehicle_weight_detail', 'payload', 'load_width', 'load_height', 'load_length',
                      'load_volume', 'trailer_load_braked', 'trailer_load_unbraked', 'max_towing_weight',
                      'max_nose_weight', 'fuel_tank_volume', 'battery_ownership', 'battery_charging_time',
                      'battery_capacity', 'battery', 'electric_range', 'electric_range_city', 'number_of_beds',
                      'number_of_axles', 'vehicle_art', 'country_code', 'postal_code', 'city', 'street',
                      'seller_name', 'license_plate', 'description', 'carpass_mileage_url',
                      'tracking_first_registration', 'tracking_fuel_type', 'tracking_image_content',
                      'tracking_smyle_eligible', 'tracking_mileage', 'tracking_price', 'tracking_model_taxonomy',
                      'tracking_boosting_product', 'tracking_relevance_adjustment', 'tracking_boost_level',
                      'tracking_applied_boost_level', 'tracking_order_bucket', 'tracking_topspot_algorithm',
                      'tracking_topspot_dealer_id', 'attr_c', 'attr_con', 'attr_nw', 'attr_bc', 'attr_yc',
                      'airbag', 'vehicle_co2_class', 'park_assist_mobile', 'export', 'sliding_door_type']

    BOOL_COLUMNS = ['particle_filter', 'new_inspection', 'service_book_maintained', 'non_smoking_vehicle',
                    'battery_certificate', 'double_cab', 'awning', 'sliding_door', 'sliding_door_right',
                    'sliding_door_left', 'abs', 'esp', 'traction_control', 'driver_airbag', 'passenger_airbag',
                    'side_airbag', 'head_airbag', 'rear_airbag', 'immobilizer', 'emergency_brake_assist',
                    'blind_spot_assist', 'lane_assist', 'distance_warning', 'traffic_sign_recognition',
                    'luggage_partition', 'folding_rear_seat', 'lumbar_support', 'tow_bar', 'wireless_phone_charging',
                    'keyless_central_locking', 'seat_ventilation', 'wind_deflector_for_convertible',
                    'hill_start_assist', 'alarm_system', 'isofix', 'isofix_passenger', 'tire_pressure_monitoring',
                    'emergency_call', 'night_vision', 'self_steering_park_assist', 'power_steering',
                    'central_locking', 'central_locking_remote', 'electric_windows', 'electric_mirrors',
                    'electric_folding_mirrors', 'auto_dimming_mirror', 'leather_steering_wheel',
                    'heated_steering_wheel', 'multifunction_steering_wheel', 'cruise_control',
                    'adaptive_cruise_control', 'speed_limiter', 'start_stop_system', 'parking_sensors_front',
                    'parking_sensors_rear', 'parking_assist', 'parking_camera', 'exit_assist',
                    'electronic_parking_brake', 'air_conditioning', 'climate_control', 'climate_control_2zone',
                    'climate_control_3zone', 'climate_control_4zone', 'heated_seats', 'heated_rear_seats',
                    'massage_seats', 'electric_seats', 'electric_seats_memory', 'sport_seats', 'armrest',
                    'foldable_passenger_seat', 'auxiliary_heating', 'heated_windshield', 'fog_lights',
                    'xenon_lights', 'bi_xenon_lights', 'led_headlights', 'full_led_headlights',
                    'led_daytime_running_lights', 'daytime_running_lights', 'adaptive_headlights', 'curve_light',
                    'high_beam_assist', 'glare_free_high_beam', 'laser_light', 'light_sensor', 'rain_sensor',
                    'ambient_lighting', 'headlight_washer', 'alloy_wheels', 'steel_wheels', 'sunroof',
                    'panoramic_roof', 'folding_roof', 'roof_rack', 'tinted_windows', 'electric_tailgate',
                    'air_suspension', 'sport_suspension', 'sport_package', 'winter_package', 'spoiler', 'ski_bag',
                    'tuning', 'radio', 'cd_player', 'multi_cd_changer', 'mp3', 'dab_radio', 'navigation_system',
                    'navigation_preparation', 'touchscreen', 'voice_control', 'bluetooth', 'handsfree', 'usb',
                    'apple_carplay', 'android_auto', 'wifi_hotspot', 'music_streaming', 'sound_system',
                    'onboard_computer', 'digital_cockpit', 'tv', 'all_season_tires', 'summer_tires',
                    'winter_tires', 'spare_wheel', 'emergency_wheel', 'tire_repair_kit', 'catalytic_converter',
                    'e10_compatible', 'all_wheel_drive', 'front_wheel_drive', 'rear_wheel_drive', 'warranty',
                    'right_hand_drive', 'taxi', 'disabled_accessible', 'smoker_package', 'leather_interior',
                    'paddle_shifters']

    def __init__(self, schema_name: str = "vehicle_marketplace", table_name: str = "vehicle_data"):
        """
        Initialize database connection with thread-safe connection pooling.

        Args:
            schema_name: Name of the schema (default: vehicle_marketplace)
            table_name: Name of the table (default: vehicle_data)
        """
        self.schema_name = schema_name
        self.table_name = table_name

        print(f"Initializing VehicleDatabase for schema: {schema_name}, table: {table_name}")

        # Get or create connection pool for this database
        pool_key = f"{self.host}:{self.port}:{self.database_name}"

        with self._lock:
            if pool_key not in self._connection_pools:
                try:
                    print(f"Creating new connection pool for {pool_key}")
                    self._connection_pools[pool_key] = pool.ThreadedConnectionPool(
                        minconn=1,
                        maxconn=20,
                        dbname=self.database_name,
                        user=self.user,
                        password=self.password,
                        host=self.host,
                        port=self.port
                    )
                    print("Connection pool created successfully")
                except Exception as e:
                    print(f"ERROR: Failed to create connection pool: {e}")
                    raise

        self.connection_pool = self._connection_pools[pool_key]

        # Initialize database structure
        self._initialize_database()

    def _get_connection(self):
        """Get a connection from the pool."""
        try:
            conn = self.connection_pool.getconn()
            return conn
        except Exception as e:
            print(f"ERROR: Failed to get connection from pool: {e}")
            raise

    def _put_connection(self, conn):
        """Return a connection to the pool."""
        try:
            self.connection_pool.putconn(conn)
        except Exception as e:
            print(f"ERROR: Failed to return connection to pool: {e}")

    def _initialize_database(self):
        """Initialize database, schema, and table if they don't exist."""
        try:
            self.check_schema_exist()
            self.create_table_if_not_exists()
            self.create_indexes()
            print("Database initialization completed successfully")
        except Exception as e:
            print(f"ERROR: Database initialization failed: {e}")
            raise

    def check_schema_exist(self):
        """Check if schema exists, create if it doesn't."""
        conn = None
        cursor = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Check if schema exists
            cursor.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name = %s",
                (self.schema_name,)
            )

            if cursor.fetchone() is None:
                print(f"Schema '{self.schema_name}' does not exist. Creating...")
                cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema_name)))
                conn.commit()
                print(f"Schema '{self.schema_name}' created successfully")
            else:
                print(f"Schema '{self.schema_name}' already exists")

        except Exception as e:
            print(f"ERROR: Failed to check/create schema: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if conn:
                self._put_connection(conn)

    def create_table_if_not_exists(self):
        """Create table if it doesn't exist."""
        conn = None
        cursor = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Build column definitions
            columns = [
                "unique_id VARCHAR(500) PRIMARY KEY NOT NULL",
                "vehicle_id VARCHAR(255) NOT NULL",
                "data_source VARCHAR(255) NOT NULL",
                "listing_url VARCHAR(255) NOT NULL",
                "images JSON",
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            ]

            # Add string columns (all nullable except vehicle_id and data_source)
            for col in self.STRING_COLUMNS:
                if col not in ['vehicle_id', 'data_source', 'listing_url', 'images']:
                    columns.append(f"{col} TEXT")

            # Add boolean columns (all nullable)
            for col in self.BOOL_COLUMNS:
                columns.append(f"{col} BOOLEAN")

            create_table_query = sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.{} (
                    {}
                )
            """).format(
                sql.Identifier(self.schema_name),
                sql.Identifier(self.table_name),
                sql.SQL(',\n                    ').join(map(sql.SQL, columns))
            )

            cursor.execute(create_table_query)
            conn.commit()
            print(f"Table '{self.schema_name}.{self.table_name}' checked/created successfully")

        except Exception as e:
            print(f"ERROR: Failed to create table: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if conn:
                self._put_connection(conn)

    def create_indexes(self):
        """Create indexes on important columns."""
        conn = None
        cursor = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # ✅ Use plain string formatting for index names
            index_queries = [
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS idx_{}_vehicle_id ON {}.{} (vehicle_id)"
                ).format(
                    sql.SQL(self.table_name),  # ✅ plain name, not Identifier
                    sql.Identifier(self.schema_name),
                    sql.Identifier(self.table_name)
                ),
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS idx_{}_data_source ON {}.{} (data_source)"
                ).format(
                    sql.SQL(self.table_name),
                    sql.Identifier(self.schema_name),
                    sql.Identifier(self.table_name)
                ),
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS idx_{}_listing_url ON {}.{} (listing_url)"
                ).format(
                    sql.SQL(self.table_name),
                    sql.Identifier(self.schema_name),
                    sql.Identifier(self.table_name)
                ),
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS idx_{}_created_at ON {}.{} (created_at)"
                ).format(
                    sql.SQL(self.table_name),
                    sql.Identifier(self.schema_name),
                    sql.Identifier(self.table_name)
                )
            ]

            for query in index_queries:
                cursor.execute(query)

            conn.commit()
            print("✅ Indexes created successfully")

        except Exception as e:
            print(f"ERROR: Failed to create indexes: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if conn:
                self._put_connection(conn)

    def generate_unique_id(self, vehicle_id: str, data_source: str) -> str:
        """
        Generate unique ID from vehicle_id and data_source.

        Args:
            vehicle_id: Vehicle ID
            data_source: Data source name

        Returns:
            Unique ID string
        """
        return f"{vehicle_id}_{data_source}"

    def check_id_exists(self, vehicle_id: str, data_source: str) -> bool:
        """
        Check if a vehicle record exists based on vehicle_id and data_source.

        Args:
            vehicle_id: Vehicle ID
            data_source: Data source name

        Returns:
            True if exists, False otherwise
        """
        conn = None
        cursor = None
        try:
            unique_id = self.generate_unique_id(vehicle_id, data_source)

            conn = self._get_connection()
            cursor = conn.cursor()

            query = sql.SQL("SELECT 1 FROM {}.{} WHERE unique_id = %s LIMIT 1").format(
                sql.Identifier(self.schema_name),
                sql.Identifier(self.table_name)
            )

            cursor.execute(query, (unique_id,))
            exists = cursor.fetchone() is not None

            return exists

        except Exception as e:
            print(f"ERROR: Failed to check if ID exists: {e}")
            raise
        finally:
            if cursor:
                cursor.close()
            if conn:
                self._put_connection(conn)

    def insert_vehicle(self, data: Dict[str, Any]) -> bool:
        """
        Insert a vehicle record into the database.

        Args:
            data: Dictionary containing vehicle data. Must include 'vehicle_id' and 'data_source'.
                  All other fields are optional.

        Returns:
            True if insertion successful, False otherwise
        """
        conn = None
        cursor = None
        try:
            # Validate required fields
            if 'vehicle_id' not in data or 'data_source' not in data:
                print("ERROR: 'vehicle_id' and 'data_source' are required fields")
                return False

            if not data['vehicle_id'] or not data['data_source']:
                print("ERROR: 'vehicle_id' and 'data_source' cannot be empty")
                return False

            # Generate unique_id
            unique_id = self.generate_unique_id(data['vehicle_id'], data['data_source'])

            # Check if already exists
            # if self.check_id_exists(data['vehicle_id'], data['data_source']):
            #     print(f"SKIP: Vehicle with ID '{unique_id}' already exists")
            #     return False

            conn = self._get_connection()
            cursor = conn.cursor()

            # Prepare data for insertion
            insert_data = {'unique_id': unique_id}
            insert_data.update(data)

            # Filter only valid columns
            valid_columns = ['unique_id', 'vehicle_id', 'data_source'] + \
                            [col for col in self.STRING_COLUMNS if col not in ['vehicle_id', 'data_source']] + \
                            self.BOOL_COLUMNS

            filtered_data = {k: v for k, v in insert_data.items() if k in valid_columns}

            # Build insert query
            columns = list(filtered_data.keys())
            values = list(filtered_data.values())

            query = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                sql.Identifier(self.schema_name),
                sql.Identifier(self.table_name),
                sql.SQL(', ').join(map(sql.Identifier, columns)),
                sql.SQL(', ').join(sql.Placeholder() * len(values))
            )

            cursor.execute(query, values)
            conn.commit()

            print(f"SUCCESS: Vehicle '{unique_id}' inserted successfully")
            return True

        except psycopg2.IntegrityError as e:
            print(f"ERROR: Integrity error during insertion: {e}")
            if conn:
                conn.rollback()
            return False
        except Exception as e:
            print(f"ERROR: Failed to insert vehicle: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                self._put_connection(conn)

    def close(self):
        """Close all connections in the pool."""
        try:
            if hasattr(self, 'connection_pool') and self.connection_pool:
                self.connection_pool.closeall()
                print("All database connections closed")
        except Exception as e:
            print(f"ERROR: Failed to close connections: {e}")


# Example usage
if __name__ == "__main__":
    # Initialize database
    db = VehicleDatabase(schema_name="vehicle_marketplace", table_name="vehicle_data")

    # Example vehicle data
    vehicle_data = {
        'vehicle_id': 'VEH123456',
        'data_source': 'AutoTrader',
        'make': 'Toyota',
        'model': 'Camry',
        'price': '25000',
        'mileage_km': '50000',
        'fuel_type': 'Petrol',
        'abs': True,
        'airbag': 'Multiple',
        'air_conditioning': True
    }

    # Check if exists
    exists = db.check_id_exists(vehicle_data['vehicle_id'], vehicle_data['data_source'])
    print(f"Vehicle exists: {exists}")

    # Insert vehicle
    if not exists:
        success = db.insert_vehicle(vehicle_data)
        print(f"Insertion result: {success}")
    else:
        print("Vehicle already exists, skipping insertion")

    # Close connections when done
    db.close()
