package com.lu.cryptobot
import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import androidx.room.*
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first

private val Context.settings by preferencesDataStore("connection")
class Settings(private val context: Context) {
    suspend fun get(key: String) = context.settings.data.first()[stringPreferencesKey(key)].orEmpty()
    suspend fun put(key: String,value: String) { context.settings.edit { it[stringPreferencesKey(key)]=value } }
}
@Entity data class AlertRecord(@PrimaryKey val eventId: String,val title: String,val message: String,val route: String,val receivedAt: Long)
@Dao interface AlertDao {
    @Insert(onConflict=OnConflictStrategy.IGNORE) suspend fun insert(value: AlertRecord): Long
    @Query("SELECT COUNT(*) FROM AlertRecord") suspend fun count(): Int
    @Query("SELECT * FROM AlertRecord ORDER BY receivedAt DESC LIMIT 100") fun watch(): Flow<List<AlertRecord>>
    @Query("DELETE FROM AlertRecord WHERE eventId NOT IN (SELECT eventId FROM AlertRecord ORDER BY receivedAt DESC LIMIT 1000)") suspend fun prune()
}
@Database(entities=[AlertRecord::class],version=1,exportSchema=false) abstract class BotDatabase: RoomDatabase() { abstract fun alerts(): AlertDao }
