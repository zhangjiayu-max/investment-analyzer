const DB_NAME = 'InvestmentAnalyzerCache'
const DB_VERSION = 1

class CacheService {
  constructor() {
    this.db = null
    this.readyPromise = this.initDB()
  }

  async initDB() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION)

      request.onerror = () => {
        console.error('IndexedDB error:', request.error)
        reject(request.error)
      }

      request.onsuccess = () => {
        this.db = request.result
        resolve(this.db)
      }

      request.onupgradeneeded = (event) => {
        const db = event.target.result

        if (!db.objectStoreNames.contains('portfolio')) {
          const portfolioStore = db.createObjectStore('portfolio', { keyPath: 'id' })
          portfolioStore.createIndex('updated_at', 'updated_at', { unique: false })
        }

        if (!db.objectStoreNames.contains('valuations')) {
          const valuationStore = db.createObjectStore('valuations', { keyPath: 'index_code' })
          valuationStore.createIndex('updated_at', 'updated_at', { unique: false })
        }

        if (!db.objectStoreNames.contains('marketData')) {
          const marketStore = db.createObjectStore('marketData', { keyPath: 'code' })
          marketStore.createIndex('updated_at', 'updated_at', { unique: false })
        }

        if (!db.objectStoreNames.contains('config')) {
          db.createObjectStore('config', { keyPath: 'key' })
        }
      }
    })
  }

  async waitReady() {
    return this.readyPromise
  }

  async savePortfolio(data) {
    await this.waitReady()
    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction(['portfolio'], 'readwrite')
      const store = transaction.objectStore('portfolio')

      data.forEach(item => {
        item.updated_at = new Date().toISOString()
        store.put(item)
      })

      transaction.oncomplete = () => resolve()
      transaction.onerror = () => reject(transaction.error)
    })
  }

  async getPortfolio() {
    await this.waitReady()
    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction(['portfolio'], 'readonly')
      const store = transaction.objectStore('portfolio')
      const request = store.getAll()

      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
  }

  async saveValuation(data) {
    await this.waitReady()
    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction(['valuations'], 'readwrite')
      const store = transaction.objectStore('valuations')

      const item = { ...data, updated_at: new Date().toISOString() }
      const request = store.put(item)

      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  }

  async getValuation(indexCode) {
    await this.waitReady()
    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction(['valuations'], 'readonly')
      const store = transaction.objectStore('valuations')
      const request = store.get(indexCode)

      request.onsuccess = () => {
        const result = request.result
        if (result) {
          const age = Date.now() - new Date(result.updated_at).getTime()
          if (age < 24 * 60 * 60 * 1000) {
            resolve(result)
          } else {
            resolve(null)
          }
        } else {
          resolve(null)
        }
      }
      request.onerror = () => reject(request.error)
    })
  }

  async saveMarketData(data) {
    await this.waitReady()
    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction(['marketData'], 'readwrite')
      const store = transaction.objectStore('marketData')

      const item = { ...data, updated_at: new Date().toISOString() }
      const request = store.put(item)

      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  }

  async getMarketData(code) {
    await this.waitReady()
    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction(['marketData'], 'readonly')
      const store = transaction.objectStore('marketData')
      const request = store.get(code)

      request.onsuccess = () => {
        const result = request.result
        if (result) {
          const age = Date.now() - new Date(result.updated_at).getTime()
          if (age < 5 * 60 * 1000) {
            resolve(result)
          } else {
            resolve(null)
          }
        } else {
          resolve(null)
        }
      }
      request.onerror = () => reject(request.error)
    })
  }

  async saveConfig(key, value) {
    await this.waitReady()
    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction(['config'], 'readwrite')
      const store = transaction.objectStore('config')
      const request = store.put({ key, value })

      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  }

  async getConfig(key) {
    await this.waitReady()
    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction(['config'], 'readonly')
      const store = transaction.objectStore('config')
      const request = store.get(key)

      request.onsuccess = () => resolve(request.result?.value)
      request.onerror = () => reject(request.error)
    })
  }

  async clearAll() {
    await this.waitReady()
    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction(['portfolio', 'valuations', 'marketData', 'config'], 'readwrite')
      transaction.objectStore('portfolio').clear()
      transaction.objectStore('valuations').clear()
      transaction.objectStore('marketData').clear()
      transaction.objectStore('config').clear()

      transaction.oncomplete = () => resolve()
      transaction.onerror = () => reject(transaction.error)
    })
  }

  async getCacheStats() {
    await this.waitReady()
    return new Promise((resolve, reject) => {
      const stats = {}
      const stores = ['portfolio', 'valuations', 'marketData', 'config']
      let completed = 0

      stores.forEach(storeName => {
        const transaction = this.db.transaction([storeName], 'readonly')
        const store = transaction.objectStore(storeName)
        const request = store.count()

        request.onsuccess = () => {
          stats[storeName] = request.result
          completed++
          if (completed === stores.length) {
            resolve(stats)
          }
        }

        request.onerror = () => {
          stats[storeName] = 0
          completed++
          if (completed === stores.length) {
            resolve(stats)
          }
        }
      })
    })
  }
}

export const cacheService = new CacheService()
