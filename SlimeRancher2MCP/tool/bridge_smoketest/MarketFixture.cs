namespace Il2Cpp
{
    public class MarketFixture
    {
        public record Item(string ReferenceId, string name);
        public struct Entry
        {
            public Item Type;
            public float InitialValue;
            public float FullSaturation;
        }

        public bool _hasInitializedMarketPrices { get; set; }
        public MarketFixture _settings => this;
        public MarketFixture PlortsTable => this;
        public Entry[] Plorts = {
            new Entry { Type = new("pink-id", "PinkPlort"), InitialValue = 7, FullSaturation = 50 },
            new Entry { Type = new("cotton-id", "CottonPlort"), InitialValue = 12, FullSaturation = 40 }
        };
        public bool IsMarketShutdown() => true;
        public bool TryGetMarketValues(Item item, out int currentValue, out int delta)
        {
            currentValue = 19;
            delta = -3;
            return item.ReferenceId == "pink-id";
        }
    }
}
