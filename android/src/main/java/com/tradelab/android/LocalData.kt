package com.tradelab.android

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase

@Entity(tableName = "market_sessions", primaryKeys = ["date", "symbol"])
data class MarketSessionEntity(
    val date: String,
    val symbol: String,
    val status: String,
    val candles: Int,
    val optionSnapshots: Int
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

@Database(entities = [MarketSessionEntity::class], version = 1, exportSchema = false)
abstract class TradeLabDatabase : RoomDatabase() {
    abstract fun marketSessionDao(): MarketSessionDao

    companion object {
        @Volatile private var INSTANCE: TradeLabDatabase? = null

        fun get(context: Context): TradeLabDatabase =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    TradeLabDatabase::class.java,
                    "tradelab.db"
                ).build().also { INSTANCE = it }
            }
    }
}
