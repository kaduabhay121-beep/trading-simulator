package com.tradelab.android

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

class TradeLabViewModel(app: Application) : AndroidViewModel(app) {
    private val db = TradeLabDatabase.get(app)
    private val sessionDao = db.marketSessionDao()
    private val paperDao = db.paperTradingDao()

    private val _sessions = MutableStateFlow<List<MarketSessionEntity>>(emptyList())
    val sessions: StateFlow<List<MarketSessionEntity>> = _sessions

    private val _account = MutableStateFlow(PaperAccountEntity())
    val account: StateFlow<PaperAccountEntity> = _account

    init {
        viewModelScope.launch {
            ensureAccount()
            refresh()
        }
    }

    private suspend fun ensureAccount() {
        val existing = paperDao.account()
        if (existing == null) {
            val fresh = PaperAccountEntity()
            paperDao.saveAccount(fresh)
            _account.value = fresh
        } else {
            _account.value = existing
        }
    }

    fun refresh() {
        viewModelScope.launch {
            _sessions.value = sessionDao.all()
            _account.value = paperDao.account() ?: PaperAccountEntity()
        }
    }

    fun resetPaperAccount() {
        viewModelScope.launch {
            val fresh = PaperAccountEntity()
            paperDao.saveAccount(fresh)
            paperDao.clearOrders()
            paperDao.clearPositions()
            _account.value = fresh
        }
    }

    fun clearLocalSessions() {
        viewModelScope.launch {
            sessionDao.clear()
            _sessions.value = emptyList()
        }
    }
}
