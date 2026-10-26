import unittest
from pipeline.taxonomy_service import classify_product_attributes


class DescriptionDeclarationTests(unittest.TestCase):
    def test_product_roles_beat_generic_vehicle_words(self):
        for title,category in [('Leaves Nail Treatment Serum','Nail Care'),
                               ('Raw African Nail Lotion Booster','Nail Care'),
                               ('Seropipe Extreme Lashes Serum','Makeup'),
                               ('Capixy Lashes Treatment Serum','Makeup'),
                               ('Hand & Nail Serum','Body Care'),
                               ('Dermedic Normacne Cleansing and Regulating Toner','Toner')]:
            with self.subTest(title=title):
                result=classify_product_attributes(title)
                self.assertEqual(result.primary_category,category)
                self.assertIn(result.classification_status,('auto_approved','excluded'))
        self.assertEqual(classify_product_attributes('Face Wash and Toner').classification_status,'review')

    def test_specific_hair_type_beats_a_generic_cream_or_serum_vehicle(self):
        for title,kind in [('Pull The Curl Trigger Definition Cream','Styling Cream'),
                           ('Hyaluron Moisture Plump Serum','Hair Care'),
                           ('Bond Smoothing Cream Step 6','Hair Styling Product'),
                           ('Color Naturals Creme 6.7','Hair Color')]:
            result=classify_product_attributes(title,product_type=kind)
            self.assertEqual((result.primary_category,result.is_skincare),('Hair Care',False))
        self.assertEqual(classify_product_attributes('Gentle Facial Cleanser',product_type='Hair Care').primary_category,'Cleanser')
        self.assertEqual(classify_product_attributes('Lucky Owl Nappy Rash Cream',product_type='Hair Care').primary_category,'Body Care')
        self.assertEqual(classify_product_attributes('Nuxe Hand & Nail Cream').primary_category,'Body Care')
        self.assertEqual(classify_product_attributes('Premier Soft Hands & Nail Cream').primary_category,'Body Care')
    def test_hygiene_merchandise_and_sun_essence_regressions(self):
        cases=[('Numbuzin NAD Peptides SPF50 Sun Essence','Skin Care','Sunscreen'),
               ('Durex Performax Condoms','Sexual Wellness','Medical Supplies'),
               ('Always Aloe Vera Long Pads','Feminine Care','Medical Supplies'),
               ('Brightening Facial Pads','Skin Care','Other Non-Skincare'),
               ('Canpol Non-Spill Sport Cup','Mother & Baby Care','Tools & Accessories'),
               ('Septona Cotton Ear Buds','Skin Care','Tools & Accessories'),
               ('Bubblzz Rainbow Bath Bomb','Skin Care','Body Care'),
               ('Bubblzz Mini Bath Bomb Set','Skin Care','Set'),
               ('Nivea Men Deep Clean Shaving Gel','Shaving & Grooming','Body Care'),
               ('Dettol Original Soap','Skin Care','Cleanser'),
               ('Vaseline Healing Jelly','Skin Care','Moisturizer')]
        for title,product_type,expected in cases:
            with self.subTest(title=title):
                result=classify_product_attributes(title,product_type=product_type)
                self.assertEqual(result.primary_category,expected)
                if expected!='Other Non-Skincare':self.assertIn(result.classification_status,('auto_approved','excluded'))

    def test_nutrient_names_do_not_make_topical_products_supplements(self):
        cases={'Dr.Melaxin Calcium Volume Multi Balm':'Moisturizer',
               'TopFace Vitamin Balm Primer Base':'Makeup',
               'Johnson Baby Oil':'Body Care',
               'Anivagene Serum For Hair':'Hair Care',
               'Sunscreen Primer SPF50':'Sunscreen'}
        for title,category in cases.items():
            with self.subTest(title=title):
                self.assertEqual(classify_product_attributes(title).primary_category,category)
        result=classify_product_attributes('Isntree Hyaluronic Acid Zinc Cooling Pad')
        self.assertEqual(result.classification_status,'review')
        self.assertNotEqual(result.primary_category,'Supplements & Health')
        self.assertEqual(classify_product_attributes('Zinc Tablets 30').primary_category,'Supplements & Health')

    def test_opening_product_declarations_resolve_unknown_titles(self):
        cases=[('URELIA 10','Exfoliating moisturizing cream for scaly skin.','Moisturizer'),
               ('TEEN DERM Gel','<p>Purifying cleansing gel for oily skin.</p><p>Use our moisturizing cream afterward.</p>','Cleanser'),
               ('NEOTONE Eyes','Lightening eye contour care. Helps dark circles.','Eye Skincare'),
               ('XERIAL 10 LAIT','Daily body moisturizer for dry skin.','Body Care'),
               ('CLINICAL TRIPLE ACTION','<h3>Triple effect serum</h3><p>Apply to cleansed skin.</p>','Serum')]
        for title,description,category in cases:
            with self.subTest(title=title):
                result=classify_product_attributes(title,description=description)
                self.assertEqual(result.primary_category,category)
                self.assertEqual(result.classification_status,'auto_approved')
                self.assertTrue(any(e['source']=='product_description_declaration' for e in result.evidence))

    def test_usage_references_and_ingredient_sections_do_not_define_product_type(self):
        for description in ['Apply with your favorite moisturizing cream.',
                            'Ingredients: thermal water, glycerin, cream fragrance.',
                            'Use with a foaming cleanser for best results.',
                            'For a superior razor experience, use this item.',
                            'Contains thermal water and vitamins.']:
            with self.subTest(description=description):
                result=classify_product_attributes('Mystery 10',description=description)
                self.assertEqual(result.classification_status,'review')

    def test_noun_in_an_incidental_shaving_reference_is_not_an_accessory(self):
        result=classify_product_attributes('Deep Clean Shaving Gel',description='Nivea Men Deep shaving gel cleans your skin for a superior razor experience.')
        self.assertNotEqual(result.primary_category,'Tools & Accessories')

    def test_explicit_title_wins_over_description_and_department(self):
        result=classify_product_attributes('Gentle Face Cleanser',product_type='Face Serum',description='A rich moisturizer cream.')
        self.assertEqual(result.primary_category,'Cleanser')

    def test_broad_department_remains_unresolved(self):
        self.assertEqual(classify_product_attributes('Blue Comfort',product_type='Mother & Baby Care').classification_status,'review')

    def test_specific_retailer_roles_resolve_line_names_without_overriding_title(self):
        for title,kind,expected in [
            ('Glycolic Acid 100ml','Face Exfoliator','Exfoliator'),
            ('Aqua Marine Jelly','Face Mist and Spray','Toner'),
            ('Travel Organizer','Makeup Bag','Tools & Accessories'),
            ('Clear Fit Master','Acne Patch','Treatment'),
            ('Gentle Facial Cleanser','Face Mist','Cleanser'),
            ('Gentle Facial Cleanser','Makeup Bag','Cleanser'),
        ]:
            with self.subTest(title=title,kind=kind):
                result=classify_product_attributes(title,product_type=kind)
                self.assertEqual(result.primary_category,expected)
                self.assertIn(result.classification_status,('auto_approved','excluded'))
        for kind in ('Face Gel','Oils','Body Spray'):
            self.assertEqual(classify_product_attributes('Original 10',product_type=kind).classification_status,'review')

    def test_explicit_retailer_types_are_bounded(self):
        for product_type,expected in [('SPF','Sunscreen'),('Shower Gel / Body Wash','Body Care'),
                ('Highlighter & Contour','Makeup'),('Hair Ampoule','Hair Care'),('Skin Tool','Tools & Accessories')]:
            result=classify_product_attributes('Original 10',product_type=product_type)
            self.assertEqual(result.primary_category,expected)
            self.assertIn(result.classification_status,('auto_approved','excluded'))




class ExplicitMerchandiseTests(unittest.TestCase):
    def test_unambiguous_merchandise_does_not_require_skincare_review(self):
        for title,category in [
            ('Izipizi #D Sunglasses','Tools & Accessories'),
            ('Canpol Babies Water Teether (3m+)','Tools & Accessories'),
            ('Canpol Babies Soother Holder','Tools & Accessories'),
            ('Lanalou Pen Control & Writing Activity German Book','Tools & Accessories'),
            ('Wooden Foot File','Tools & Accessories'),
            ('Elastic Ankle Support','Medical Supplies'),
            ('Freshdays 72 Pantyliner','Medical Supplies'),
            ('Splendour Lavender Reed Diffusers','Fragrance'),
            ('Flormar Matte Setting Spray','Makeup'),
            ('TopFace BB Skin Editor Matte Finish','Makeup'),
            ('Note Brow Pomade','Makeup'),
            ('Lip Liner and Lip Gloss','Makeup'),
        ]:
            with self.subTest(title=title):
                result=classify_product_attributes(title)
                self.assertEqual(result.primary_category,category)
                self.assertFalse(result.is_skincare)
                self.assertEqual(result.classification_status,'excluded')


class CreamyWashTests(unittest.TestCase):
    def test_creamy_wash_is_a_cleanser_even_when_instructions_mention_sunscreen(self):
        result=classify_product_attributes('Panoxyl 4% Benzoyl Peroxide Acne Creamy Wash',description='Use sunscreen after use. Peeling may occur.')
        self.assertEqual(result.primary_category,'Cleanser')
        self.assertTrue(result.is_skincare)
        self.assertEqual(result.classification_status,'auto_approved')


class AnatomicalRoleModifiersTests(unittest.TestCase):
    def test_bounded_modifiers_do_not_hide_hand_body_or_foot_roles(self):
        for title in ['Avene Cicalfate Hand Repairing Barrier Cream', 'Glysolid Body Musk Lotion', 'Infinity Naturals Foot Scrub']:
            with self.subTest(title=title):
                result=classify_product_attributes(title)
                self.assertEqual(result.primary_category,'Body Care')
                self.assertTrue(result.is_skincare)
        self.assertEqual(classify_product_attributes('Essence Shine Lipgloss').primary_category,'Makeup')
        self.assertEqual(classify_product_attributes('The Body Shop Vitamin E Face Cream').primary_category,'Moisturizer')


class BrandFunctionSeparationTests(unittest.TestCase):
    def test_cosmetics_brand_essence_does_not_turn_lip_or_nail_products_into_toners(self):
        for title,category in [
            ('Essence Juicy Serum Hydrating Lip Treatment','Lip Care'),
            ('Essence Juicy Matte Lip Mousse','Makeup'),
            ('Essence Juicy Tint Lip Marker','Makeup'),
            ('Essence Juicy Nail Balm','Nail Care'),
            ('Essence Hydra Kiss Lip Oil Set - 3 Pcs','Set'),
            ('Sheglam Tint Lock Tattoo Peel Off Brow Gel','Makeup'),
            ('Sheglam Air Plush Lip & Cheek Cream','Makeup'),
        ]:
            with self.subTest(title=title):
                brand='Essence' if title.startswith('Essence') else 'SHEGLAM'
                result=classify_product_attributes(title,brand)
                self.assertEqual(result.primary_category,category)
                self.assertIn(result.classification_status,('auto_approved','excluded'))

    def test_actual_essence_product_remains_toner_and_unknown_name_stays_uncertain(self):
        result=classify_product_attributes('COSRX Advanced Snail 96 Mucin Power Essence','COSRX')
        self.assertEqual(result.primary_category,'Toner')
        self.assertEqual(classify_product_attributes('Essence Mystery Product','Essence').classification_status,'review')


class DecorativeRoleTests(unittest.TestCase):
    def test_explicit_makeup_and_accessories_cannot_become_toners(self):
        cases=[('Lash Princess False Lash Effect','Makeup'),
               ('Kajal Kohl Pencil','Makeup'),('Skin Tint','Makeup'),
               ('Brow Lift & Freeze Glue','Makeup'),('Inner Eye Brightening Pen','Makeup'),
               ('Highlighter Stick','Makeup'),('Ink Liner','Makeup'),
               ('Makeup Fixing Spray','Makeup'),('Brow Lifting Gel Set.','Set'),
               ('Duo Sharpener','Tools & Accessories'),('Powder Puff','Tools & Accessories'),
               ('Magnetic Lashes Storage Case','Tools & Accessories')]
        for title,category in cases:
            with self.subTest(title=title):
                result=classify_product_attributes('Essence '+title,'Essence')
                self.assertEqual(result.primary_category,category)
                self.assertFalse(result.is_skincare)


if __name__=='__main__':unittest.main()
