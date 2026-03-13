from Classes.Ingestors.faa.SwimIngestor import getSwimIngestor

if __name__ == "__main__":
    ingestor = getSwimIngestor()
    ingestor._running = True
    ingestor.run()