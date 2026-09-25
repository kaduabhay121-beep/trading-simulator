package com.tradelab.android

import android.content.Context
import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Entity(tableName = "market_sessions", primaryKeys = ["date", "symbol"])
data class MarketSessionEntity(
    val date: String,
    val symbol: String,
    val status: String,
    val candles: Int,
    val optionSnapshots: Int
)

@Entity(
    tableName = "market_candles",
    primaryKeys = ["symbol", "timeframe", "timestamp"]
)
data class MarketCandleEntity(
    val symbol: String,
    val timeframe: Int,
    val timestamp: Long,
    val open: Double,
    val high: Double,
    val low: Double,
    val close: Double,
    val volume: Long,
    val source: String,
    val sessionDate: String
)

@Entity(
    tableName = "option_snapshots",
    primaryKeys = ["underlying", "expiry", "timestamp"]
)
data class OptionSnapshotEntity(
    val underlying: String,
    val expiry: String,
    val timestamp: Long,
    val payloadJson: String,
    val source: String
)

@Entity(tableName = "paper_account")
data class PaperAccountEntity(
    @PrimaryKey val id: Int = 1,
    val initialBalance: Double = 1_000_000.0,
    val balance: Double = 1_000_000.0,
    val usedMargin: Double = 0.0,
    val realizedPnl: Double = 0.0,
    val updatedAt: Long = System.currentTimeMillis()
)

@Entity(tableName = "paper_orders")
data class PaperOrderEntity(
    @PrimaryKey val id: String,
    val symbol: String,
    val side: String,
    val quantity: Int,
    val orderType: String,
    val price: Double,
    val stopLoss: Double?,
    val target: Double?,
    val status: String,
    val createdAt: Long,
    val filledAt: Long?,
    val source: String
)

@Entity(tableName = "paper_positions")
data class PaperPositionEntity(
    @PrimaryKey val id: String,
    val symbol: String,
    val side: String,
    val quantity: Int,
    val entryPrice: Double,
    val lastPrice: Double,
    val stopLoss: Double?,
    val target: Double?,
    val realizedPnl: Double = 0.0,
    val updatedAt: Long
)

@Entity(tableName = "journal_events")
data class JournalEventEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val timestamp: Long,
    val eventType: String,
    val symbol: String?,
    val payloadJson: String,
    val source: String
)

@Entity(tableName = "replay_sessions")
data class ReplaySessionEntity(
    @PrimaryKey val id: String,
    val symbol: String,
    val sessionDate: String,
    val timeframe: Int,
    val replayIndex: Int,
    val replaySpeed: Double,
    val status: String,
    val realizedPnl: Double,
    val updatedAt: Long
)

@Dao
interface MarketSessionDao {
    @Query("SELECT * FROM market_sessions ORDER BY date DESC, symbol ASC")
    suspend fun all(): List<MarketSessionEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(session: MarketSessionEntity)

    @Query("DELETE FROM market_sessions")
    suspend fun clear()
}

@Dao
interface MarketDataDao {
    @Query("SELECT * FROM market_candles WHERE symbol = :symbol AND timeframe = :timeframe ORDER BY timestamp ASC")
    fun candles(symbol: String, timeframe: Int): Flow<List<MarketCandleEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertCandles(rows: List<MarketCandleEntity>)

    @Query("DELETE FROM market_candles WHERE symbol = :symbol AND sessionDate = :sessionDate")
    suspend fun deleteSession(symbol: String, sessionDate: String)

    @Query("SELECT COUNT(*) FROM market_candles WHERE symbol = :symbol AND sessionDate = :sessionDate")
    suspend fun candleCount(symbol: String, sessionDate: String): Int

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertOptionSnapshots(rows: List<OptionSnapshotEntity>)

    @Query("SELECT * FROM option_snapshots WHERE underlying = :underlying AND timestamp <= :atTime ORDER BY timestamp DESC LIMIT 1")
    suspend fun latestOptionSnapshot(underlying: String, atTime: Long): OptionSnapshotEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertSession(session: MarketSessionEntity)
}

@Dao
interface PaperTradingDao {
    @Query("SELECT * FROM paper_account WHERE id = 1")
    suspend fun account(): PaperAccountEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveAccount(account: PaperAccountEntity)

    @Query("SELECT * FROM paper_orders ORDER BY createdAt DESC")
    fun orders(): Flow<List<PaperOrderEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveOrder(order: PaperOrderEntity)

    @Query("DELETE FROM paper_orders")
    suspend fun clearOrders()

    @Query("SELECT * FROM paper_positions ORDER BY updatedAt DESC")
    fun positions(): Flow<List<PaperPositionEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun savePosition(position: PaperPositionEntity)

    @Query("DELETE FROM paper_positions")
    suspend fun clearPositions()

    @Insert
    suspend fun journal(event: JournalEventEntity)

    @Query("SELECT * FROM journal_events ORDER BY timestamp DESC LIMIT :limit")
    suspend fun recentJournal(limit: Int = 200): List<JournalEventEntity>
}

@Dao
interface ReplayDao {
    @Query("SELECT * FROM replay_sessions ORDER BY updatedAt DESC")
    fun sessions(): Flow<List<ReplaySessionEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun save(session: ReplaySessionEntity)

    @Query("DELETE FROM replay_sessions WHERE id = :id")
    suspend fun delete(id: String)
}

@Database(
    entities = [
        MarketSessionEntity::class,
        MarketCandleEntity::class,
        OptionSnapshotEntity::class,
        PaperAccountEntity::class,
        PaperOrderEntity::class,
        PaperPositionEntity::class,
        JournalEventEntity::class,
        ReplaySessionEntity::class
    ],
    version = 2,
    exportSchema = false
)
abstract class TradeLabDatabase : RoomDatabase() {
    abstract fun marketSessionDao(): MarketSessionDao
    abstract fun marketDataDao(): MarketDataDao
    abstract fun paperTradingDao(): PaperTradingDao
    abstract fun replayDao(): ReplayDao

    companion object {
        @Volatile private var INSTANCE: TradeLabDatabase? = null

        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("CREATE TABLE IF NOT EXISTS market_candles (symbol TEXT NOT NULL, timeframe INTEGER NOT NULL, timestamp INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL, source TEXT NOT NULL, sessionDate TEXT NOT NULL, PRIMARY KEY(symbol, timeframe, timestamp))")
                db.execSQL("CREATE TABLE IF NOT EXISTS option_snapshots (underlying TEXT NOT NULL, expiry TEXT NOT NULL, timestamp INTEGER NOT NULL, payloadJson TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(underlying, expiry, timestamp))")
                db.execSQL("CREATE TABLE IF NOT EXISTS paper_account (id INTEGER NOT NULL, initialBalance REAL NOT NULL, balance REAL NOT NULL, usedMargin REAL NOT NULL, realizedPnl REAL NOT NULL, updatedAt INTEGER NOT NULL, PRIMARY KEY(id))")
                db.execSQL("INSERT OR IGNORE INTO paper_account VALUES(1,1000000.0,1000000.0,0.0,0.0,strftime('%s','now')*1000)")
                db.execSQL("CREATE TABLE IF NOT EXISTS paper_orders (id TEXT NOT NULL PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, orderType TEXT NOT NULL, price REAL NOT NULL, stopLoss REAL, target REAL, status TEXT NOT NULL, createdAt INTEGER NOT NULL, filledAt INTEGER, source TEXT NOT NULL)")
                db.execSQL("CREATE TABLE IF NOT EXISTS paper_positions (id TEXT NOT NULL PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, entryPrice REAL NOT NULL, lastPrice REAL NOT NULL, stopLoss REAL, target REAL, realizedPnl REAL NOT NULL, updatedAt INTEGER NOT NULL)")
                db.execSQL("CREATE TABLE IF NOT EXISTS journal_events (id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, timestamp INTEGER NOT NULL, eventType TEXT NOT NULL, symbol TEXT, payloadJson TEXT NOT NULL, source TEXT NOT NULL)")
                db.execSQL("CREATE TABLE IF NOT EXISTS replay_sessions (id TEXT NOT NULL PRIMARY KEY, symbol TEXT NOT NULL, sessionDate TEXT NOT NULL, timeframe INTEGER NOT NULL, replayIndex INTEGER NOT NULL, replaySpeed REAL NOT NULL, status TEXT NOT NULL, realizedPnl REAL NOT NULL, updatedAt INTEGER NOT NULL)")
            }
        }

        fun get(context: Context): TradeLabDatabase =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    TradeLabDatabase::class.java,
                    "tradelab.db"
                ).addMigrations(MIGRATION_1_2).build().also { INSTANCE = it }
            }
    }
}
